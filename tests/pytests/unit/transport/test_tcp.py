import asyncio
import os
import socket
import warnings

import attr
import pytest
import tornado
import tornado.concurrent
import tornado.gen
import tornado.ioloop
import tornado.iostream
from pytestshellutils.utils import ports

import salt.channel.server
import salt.exceptions
import salt.transport.tcp
import salt.utils.platform
from tests.support.mock import MagicMock, PropertyMock, patch

pytestmark = [
    pytest.mark.core_test,
]


@pytest.fixture
def _fake_keys():
    with patch("salt.crypt.AsyncAuth.get_keys", autospec=True):
        yield


@pytest.fixture
def fake_crypto():
    with patch("salt.transport.tcp.PKCS1_OAEP", create=True) as fake_crypto:
        yield fake_crypto


@pytest.fixture
def _fake_authd(io_loop):
    @tornado.gen.coroutine
    def return_nothing():
        raise tornado.gen.Return()

    with patch(
        "salt.crypt.AsyncAuth.authenticated", new_callable=PropertyMock
    ) as mock_authed, patch(
        "salt.crypt.AsyncAuth.authenticate",
        autospec=True,
        return_value=return_nothing(),
    ), patch(
        "salt.crypt.AsyncAuth.gen_token", autospec=True, return_value=42
    ):
        mock_authed.return_value = False
        yield


@pytest.fixture
def _fake_crypticle():
    with patch("salt.crypt.Crypticle") as fake_crypticle:
        fake_crypticle.generate_key_string.return_value = "fakey fake"
        yield fake_crypticle


@pytest.fixture
def _squash_exepected_message_client_warning():
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="MessageClient has been deprecated and will be removed.",
            category=DeprecationWarning,
            module="salt.transport.tcp",
        )
        yield


@attr.s(frozen=True, slots=True)
class ClientSocket:
    listen_on = attr.ib(init=False, default="127.0.0.1")
    port = attr.ib(init=False, default=attr.Factory(ports.get_unused_localhost_port))
    sock = attr.ib(init=False, repr=False)

    @sock.default
    def _sock_default(self):
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def __enter__(self):
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.listen_on, self.port))
        self.sock.listen(1)
        return self

    def __exit__(self, *args):
        self.sock.close()


@pytest.fixture
def client_socket():
    with ClientSocket() as _client_socket:
        yield _client_socket


def test_get_socket():
    socket = salt.transport.tcp._get_socket({"ipv6": True})

    if salt.utils.platform.is_windows():
        assert int(socket.family) == 23
    else:
        assert int(socket.family) == 10

    socket = salt.transport.tcp._get_socket({"ipv6": False})
    assert int(socket.family) == 2


def test_get_bind_addr():
    opts = {"interface": "192.168.0.1", "tcp": 1}
    res = salt.transport.tcp._get_bind_addr(opts=opts, port_type="tcp")
    assert res == ("192.168.0.1", 1)


@pytest.mark.usefixtures("_squash_exepected_message_client_warning")
async def test_message_client_cleanup_on_close(client_socket, temp_salt_master):
    """
    test message client cleanup on close
    """

    opts = dict(temp_salt_master.config.copy(), transport="tcp")
    client = salt.transport.tcp.MessageClient(
        opts, client_socket.listen_on, client_socket.port
    )

    assert client._closed is False
    assert client._closing is False
    assert client._stream is None

    await client.connect()

    # Ensure we are testing the _read_until_future and io_loop teardown
    assert client._stream is not None

    client.close()
    assert client._closed is False
    assert client._closing is True
    assert client._stream is not None
    await asyncio.sleep(0.1)

    assert client._closed is True
    assert client._closing is False
    assert client._stream is None


async def test_async_tcp_pub_channel_connect_publish_port(
    temp_salt_master, client_socket
):
    """
    test when publish_port is not 4506
    """
    opts = dict(
        temp_salt_master.config.copy(),
        master_uri="tcp://127.0.0.1:1234",
        master_ip="127.0.0.1",
        publish_port=1234,
        transport="tcp",
        acceptance_wait_time=5,
        acceptance_wait_time_max=5,
    )
    patch_auth = MagicMock(return_value=True)
    transport = MagicMock(spec=salt.transport.tcp.TCPPubClient)
    transport.connect = MagicMock()
    future = asyncio.Future()
    transport.connect.return_value = future
    future.set_result(True)
    with patch("salt.crypt.AsyncAuth.gen_token", patch_auth), patch(
        "salt.crypt.AsyncAuth.authenticated", patch_auth
    ), patch("salt.transport.tcp.PublishClient", transport):
        channel = salt.channel.client.AsyncPubChannel.factory(opts)
        with channel:
            # We won't be able to succeed the connection because we're not mocking the tornado coroutine
            with pytest.raises(salt.exceptions.SaltClientError):
                await channel.connect()
    # The first call to the mock is the instance's __init__, and the first argument to those calls is the opts dict
    await asyncio.sleep(0.3)
    assert channel.transport.connect.call_args[0][0] == opts["publish_port"]
    transport.close()


def test_tcp_pub_server_channel_publish_filtering(temp_salt_master):
    opts = dict(
        temp_salt_master.config.copy(),
        sign_pub_messages=False,
        transport="tcp",
        acceptance_wait_time=5,
        acceptance_wait_time_max=5,
    )
    with patch("salt.master.SMaster.secrets") as secrets, patch(
        "salt.crypt.Crypticle"
    ) as crypticle, patch("salt.utils.asynchronous.SyncWrapper") as SyncWrapper:
        channel = salt.channel.server.PubServerChannel.factory(opts)
        wrap = MagicMock()
        crypt = MagicMock()
        crypt.dumps.return_value = {"test": "value"}

        secrets.return_value = {"aes": {"secret": None}}
        crypticle.return_value = crypt
        SyncWrapper.return_value = wrap

        # try simple publish with glob tgt_type
        payload = channel.wrap_payload(
            {"test": "value", "tgt_type": "glob", "tgt": "*"}
        )

        # verify we send it without any specific topic
        assert "topic_lst" in payload
        assert payload["topic_lst"] == []  # "minion01"]

        # try simple publish with list tgt_type
        payload = channel.wrap_payload(
            {"test": "value", "tgt_type": "list", "tgt": ["minion01"]}
        )

        # verify we send it with correct topic
        assert "topic_lst" in payload
        assert payload["topic_lst"] == ["minion01"]

        # try with syndic settings
        opts["order_masters"] = True
        channel = salt.channel.server.PubServerChannel.factory(opts)
        payload = channel.wrap_payload(
            {"test": "value", "tgt_type": "list", "tgt": ["minion01"]}
        )

        # verify we send it without topic for syndics
        assert "topic_lst" not in payload


def test_tcp_pub_server_channel_publish_filtering_str_list(temp_salt_master):
    opts = dict(
        temp_salt_master.config.copy(),
        transport="tcp",
        sign_pub_messages=False,
        acceptance_wait_time=5,
        acceptance_wait_time_max=5,
    )
    with patch("salt.master.SMaster.secrets") as secrets, patch(
        "salt.crypt.Crypticle"
    ) as crypticle, patch("salt.utils.asynchronous.SyncWrapper") as SyncWrapper, patch(
        "salt.utils.minions.CkMinions.check_minions"
    ) as check_minions:
        channel = salt.channel.server.PubServerChannel.factory(opts)
        wrap = MagicMock()
        crypt = MagicMock()
        crypt.dumps.return_value = {"test": "value"}

        secrets.return_value = {"aes": {"secret": None}}
        crypticle.return_value = crypt
        SyncWrapper.return_value = wrap
        check_minions.return_value = {"minions": ["minion02"]}

        # try simple publish with list tgt_type
        payload = channel.wrap_payload(
            {"test": "value", "tgt_type": "list", "tgt": "minion02"}
        )

        # verify we send it with correct topic
        assert "topic_lst" in payload
        assert payload["topic_lst"] == ["minion02"]

        # verify it was correctly calling check_minions
        check_minions.assert_called_with("minion02", tgt_type="list")


@pytest.fixture(scope="function")
def salt_message_client():
    io_loop_mock = MagicMock(spec=tornado.ioloop.IOLoop)
    io_loop_mock.asyncio_loop = None
    io_loop_mock.call_later.side_effect = lambda *args, **kwargs: (args, kwargs)

    client = salt.transport.tcp.MessageClient(
        {}, "127.0.0.1", ports.get_unused_localhost_port(), io_loop=io_loop_mock
    )

    try:
        yield client
    finally:
        client.close()


# XXX we don't return a future anymore, this needs a different way of testing.
# def test_send_future_set_retry(salt_message_client):
#    future = salt_message_client.send({"some": "message"}, tries=10, timeout=30)
#
#    # assert we have proper props in future
#    assert future.tries == 10
#    assert future.timeout == 30
#    assert future.attempts == 0
#
#    # assert the timeout callback was created
#    assert len(salt_message_client.send_queue) == 1
#    message_id = salt_message_client.send_queue.pop()[0]
#
#    assert message_id in salt_message_client.send_timeout_map
#
#    timeout = salt_message_client.send_timeout_map[message_id]
#    assert timeout[0][0] == 30
#    assert timeout[0][2] == message_id
#    assert timeout[0][3] == {"some": "message"}
#
#    # try again, now with set future
#    future.attempts = 1
#
#    future = salt_message_client.send(
#        {"some": "message"}, tries=10, timeout=30, future=future
#    )
#
#    # assert we have proper props in future
#    assert future.tries == 10
#    assert future.timeout == 30
#    assert future.attempts == 1
#
#    # assert the timeout callback was created
#    assert len(salt_message_client.send_queue) == 1
#    message_id_new = salt_message_client.send_queue.pop()[0]
#
#    # check new message id is generated
#    assert message_id != message_id_new
#
#    assert message_id_new in salt_message_client.send_timeout_map
#
#    timeout = salt_message_client.send_timeout_map[message_id_new]
#    assert timeout[0][0] == 30
#    assert timeout[0][2] == message_id_new
#    assert timeout[0][3] == {"some": "message"}


# def test_timeout_message_retry(salt_message_client):
#    # verify send is triggered with first retry
#    msg = {"some": "message"}
#    future = salt_message_client.send(msg, tries=1, timeout=30)
#    assert future.attempts == 0
#
#    timeout = next(iter(salt_message_client.send_timeout_map.values()))
#    message_id_1 = timeout[0][2]
#    message_body_1 = timeout[0][3]
#
#    assert message_body_1 == msg
#
#    # trigger timeout callback
#    salt_message_client.timeout_message(message_id_1, message_body_1)
#
#    # assert send got called, yielding potentially new message id, but same message
#    future_new = next(iter(salt_message_client.send_future_map.values()))
#    timeout_new = next(iter(salt_message_client.send_timeout_map.values()))
#
#    message_id_2 = timeout_new[0][2]
#    message_body_2 = timeout_new[0][3]
#
#    assert future_new.attempts == 1
#    assert future.tries == future_new.tries
#    assert future.timeout == future_new.timeout
#
#    assert message_body_1 == message_body_2
#
#    # now try again, should not call send
#    with contextlib.suppress(salt.exceptions.SaltReqTimeoutError):
#        salt_message_client.timeout_message(message_id_2, message_body_2)
#        raise future_new.exception()
#
#    # assert it's really "consumed"
#    assert message_id_2 not in salt_message_client.send_future_map
#    assert message_id_2 not in salt_message_client.send_timeout_map


@pytest.mark.usefixtures("_squash_exepected_message_client_warning")
def test_timeout_message_unknown_future(salt_message_client):
    #    # test we don't fail on unknown message_id
    #    salt_message_client.timeout_message(-1, "message")

    # if we do have the actual future stored under the id, but it's none
    # we shouldn't fail as well
    message_id = 1
    future = tornado.concurrent.Future()
    future.attempts = 1
    future.tries = 1
    salt_message_client.send_future_map[message_id] = future

    salt_message_client.timeout_message(message_id, "message")

    assert message_id not in salt_message_client.send_future_map


@pytest.mark.usefixtures("_squash_exepected_message_client_warning")
def xtest_client_reconnect_backoff(client_socket):
    opts = {"tcp_reconnect_backoff": 5}

    client = salt.transport.tcp.MessageClient(
        opts, client_socket.listen_on, client_socket.port
    )

    def _sleep(t):
        client.close()
        assert t == 5
        return
        # return tornado.gen.sleep()

    @tornado.gen.coroutine
    def connect(*args, **kwargs):
        raise Exception("err")

    client._tcp_client.connect = connect

    try:
        with patch("tornado.gen.sleep", side_effect=_sleep):
            client.io_loop.run_sync(client.connect)
    finally:
        client.close()


@pytest.mark.usefixtures("_fake_crypticle", "_fake_keys")
async def test_when_async_req_channel_with_syndic_role_should_use_syndic_master_pub_file_to_verify_master_sig(
    fake_crypto,
):
    # Syndics use the minion pki dir, but they also create a syndic_master.pub
    # file for comms with the Salt master
    expected_pubkey_path = os.path.join("/etc/salt/pki/minion", "syndic_master.pub")
    fake_crypto.new.return_value.decrypt.return_value = "decrypted_return_value"
    mockloop = MagicMock()
    opts = {
        "master_uri": "tcp://127.0.0.1:4506",
        "interface": "127.0.0.1",
        "ret_port": 4506,
        "ipv6": False,
        "sock_dir": ".",
        "pki_dir": "/etc/salt/pki/minion",
        "id": "syndic",
        "__role": "syndic",
        "keysize": 4096,
        "transport": "tcp",
        "acceptance_wait_time": 30,
        "acceptance_wait_time_max": 30,
        "signing_algorithm": "MOCK",
        "keys.cache_driver": "localfs_key",
    }
    client = salt.channel.client.ReqChannel.factory(opts, io_loop=mockloop)
    assert client.master_pubkey_path == expected_pubkey_path
    with patch("salt.crypt.PublicKey.from_file", return_value=MagicMock()) as mock:
        client.verify_signature("mockdata", "mocksig")
        assert mock.call_args_list[0][0][0] == expected_pubkey_path


@pytest.mark.usefixtures("_fake_authd", "_fake_crypticle", "_fake_keys")
async def test_mixin_should_use_correct_path_when_syndic():
    mockloop = MagicMock()
    expected_pubkey_path = os.path.join("/etc/salt/pki/minion", "syndic_master.pub")
    opts = {
        "master_uri": "tcp://127.0.0.1:4506",
        "interface": "127.0.0.1",
        "ret_port": 4506,
        "ipv6": False,
        "sock_dir": ".",
        "pki_dir": "/etc/salt/pki/minion",
        "id": "syndic",
        "__role": "syndic",
        "keysize": 4096,
        "sign_pub_messages": True,
        "transport": "tcp",
        "keys.cache_driver": "localfs_key",
    }
    client = salt.channel.client.AsyncPubChannel.factory(opts, io_loop=mockloop)
    client.master_pubkey_path = expected_pubkey_path
    payload = {
        "sig": "abc",
        "load": {"foo": "bar"},
        "sig_algo": salt.crypt.PKCS1v15_SHA224,
    }
    with patch("salt.crypt.verify_signature") as mock:
        client._verify_master_signature(payload)
        assert mock.call_args_list[0][0][0] == expected_pubkey_path


@pytest.mark.usefixtures("_squash_exepected_message_client_warning")
def test_presence_events_callback_passed(temp_salt_master, salt_message_client):
    opts = dict(temp_salt_master.config.copy(), transport="tcp", presence_events=True)
    channel = salt.channel.server.PubServerChannel.factory(opts)
    channel.transport = salt.transport.tcp.TCPPublishServer(opts)
    mock_publish_daemon = MagicMock()
    with patch(
        "salt.transport.tcp.TCPPublishServer.publish_daemon", mock_publish_daemon
    ):
        channel._publish_daemon()
        mock_publish_daemon.assert_called_with(
            channel.publish_payload,
            channel.presence_callback,
            channel.remove_presence_callback,
        )


async def test_presence_removed_on_stream_closed():
    opts = {"presence_events": True}

    io_loop_mock = MagicMock(spec=tornado.ioloop.IOLoop)

    with patch("salt.master.AESFuncs.__init__", return_value=None):
        server = salt.transport.tcp.PubServer(opts, io_loop=io_loop_mock)
        server._closing = True
        server.remove_presence_callback = MagicMock()

    client = salt.transport.tcp.Subscriber(tornado.iostream.IOStream, "1.2.3.4")
    client._closing = True
    server.clients = {client}

    io_loop = tornado.ioloop.IOLoop.current()
    package = {
        "topic_lst": [],
        "payload": "test-payload",
    }

    with patch("salt.transport.frame.frame_msg", return_value="framed-payload"):
        with patch(
            "tornado.iostream.BaseIOStream.write",
            side_effect=tornado.iostream.StreamClosedError(),
        ):
            await server.publish_payload(package, None)

            server.remove_presence_callback.assert_called_with(client)


async def test_tcp_pub_client_decode_dict(minion_opts, io_loop, tmp_path):
    dmsg = {"meh": "bah"}
    with salt.transport.tcp.TCPPubClient(minion_opts, io_loop, path=tmp_path) as client:
        ret = client._decode_messages(dmsg)
        assert ret == dmsg


async def test_tcp_pub_client_decode_msgpack(minion_opts, io_loop, tmp_path):
    dmsg = {"meh": "bah"}
    msg = salt.payload.dumps(dmsg)
    with salt.transport.tcp.TCPPubClient(minion_opts, io_loop, path=tmp_path) as client:
        ret = client._decode_messages(msg)
        assert ret == dmsg


def test_tcp_pub_client_close(minion_opts, io_loop, tmp_path):
    client = salt.transport.tcp.TCPPubClient(minion_opts, io_loop, path=tmp_path)

    stream = MagicMock()

    client._stream = stream
    client.close()
    assert client._closing is True
    assert client._stream is None
    client.close()
    stream.close.assert_called_once_with()


async def test_pub_server__stream_read(master_opts, io_loop):

    messages = [salt.transport.frame.frame_msg({"foo": "bar"})]

    class Stream:
        def __init__(self, messages):
            self.messages = messages

        def read_bytes(self, *args, **kwargs):
            if self.messages:
                msg = self.messages.pop(0)
                future = tornado.concurrent.Future()
                future.set_result(msg)
                return future
            raise tornado.iostream.StreamClosedError()

    client = MagicMock()
    client.stream = Stream(messages)
    client.address = "client address"
    server = salt.transport.tcp.PubServer(master_opts, io_loop)
    await server._stream_read(client)
    client.close.assert_called_once()


async def test_pub_server__stream_read_exception(master_opts, io_loop):
    client = MagicMock()
    client.stream = MagicMock()
    client.stream.read_bytes = MagicMock(
        side_effect=[
            Exception("Something went wrong"),
            tornado.iostream.StreamClosedError(),
        ]
    )
    client.address = "client address"
    server = salt.transport.tcp.PubServer(master_opts, io_loop)
    await server._stream_read(client)
    client.close.assert_called_once()


async def test_salt_message_server(master_opts):

    received = []

    def handler(stream, body, header):

        received.append(body)

    server = salt.transport.tcp.SaltMessageServer(handler)
    msg = {"foo": "bar"}
    messages = [salt.transport.frame.frame_msg(msg)]

    class Stream:
        def __init__(self, messages):
            self.messages = messages

        def read_bytes(self, *args, **kwargs):
            if self.messages:
                msg = self.messages.pop(0)
                future = tornado.concurrent.Future()
                future.set_result(msg)
                return future
            raise tornado.iostream.StreamClosedError()

    stream = Stream(messages)
    address = "client address"

    await server.handle_stream(stream, address)

    # Let loop iterate so callback gets called
    await tornado.gen.sleep(0.01)

    assert received
    assert [msg] == received


async def test_salt_message_server_exception(master_opts, io_loop):
    received = []

    def handler(stream, body, header):

        received.append(body)

    stream = MagicMock()
    stream.read_bytes = MagicMock(
        side_effect=[
            Exception("Something went wrong"),
        ]
    )
    address = "client address"
    server = salt.transport.tcp.SaltMessageServer(handler)
    await server.handle_stream(stream, address)
    stream.close.assert_called_once()


@pytest.mark.usefixtures("_squash_exepected_message_client_warning")
async def test_message_client_stream_return_exception(minion_opts, io_loop):
    msg = {"foo": "bar"}
    payload = salt.transport.frame.frame_msg(msg)
    future = tornado.concurrent.Future()
    future.set_result(payload)
    client = salt.transport.tcp.MessageClient(
        minion_opts,
        "127.0.0.1",
        12345,
        connect_callback=MagicMock(),
        disconnect_callback=MagicMock(),
    )
    client._stream = MagicMock()
    client._stream.read_bytes.side_effect = [
        future,
    ]
    try:
        io_loop.add_callback(client._stream_return)
        await tornado.gen.sleep(0.01)
        client.close()
        await tornado.gen.sleep(0.01)
        assert client._stream is None
    finally:
        client.close()


def test_tcp_pub_server_pre_fork(master_opts):
    process_manager = MagicMock()
    server = salt.transport.tcp.TCPPublishServer(master_opts)
    server.pre_fork(process_manager)


async def test_pub_server_publish_payload(master_opts, io_loop):
    server = salt.transport.tcp.PubServer(master_opts, io_loop=io_loop)
    package = {"foo": "bar"}
    topic_list = ["meh"]
    future = tornado.concurrent.Future()
    future.set_result(None)
    client = MagicMock()
    client.stream = MagicMock()
    client.stream.write.side_effect = [future]
    client.id_ = "meh"
    server.clients = [client]
    await server.publish_payload(package, topic_list)
    client.stream.write.assert_called_once()


async def test_pub_server_publish_payload_closed_stream(master_opts, io_loop):
    server = salt.transport.tcp.PubServer(master_opts, io_loop=io_loop)
    package = {"foo": "bar"}
    topic_list = ["meh"]
    client = MagicMock()
    client.stream = MagicMock()
    client.stream.write.side_effect = [
        tornado.iostream.StreamClosedError("mock"),
    ]
    client.id_ = "meh"
    server.clients = {client}
    await server.publish_payload(package, topic_list)
    assert server.clients == set()


async def test_pub_server_paths_no_perms(master_opts, io_loop):
    def publish_payload(payload):
        return payload

    pubserv = salt.transport.tcp.PublishServer(
        master_opts,
        pub_host="127.0.0.1",
        pub_port=5151,
        pull_host="127.0.0.1",
        pull_port=5152,
    )
    assert pubserv.pull_path is None
    assert pubserv.pub_path is None
    with patch("os.chmod") as p:
        await pubserv.publisher(publish_payload)
        assert p.call_count == 0


@pytest.mark.skip_on_windows()
async def test_pub_server_publisher_pull_path_perms(master_opts, io_loop, tmp_path):
    def publish_payload(payload):
        return payload

    pull_path = str(tmp_path / "pull.ipc")
    pull_path_perms = 0o664
    pubserv = salt.transport.tcp.PublishServer(
        master_opts,
        pub_host="127.0.0.1",
        pub_port=5151,
        pull_host=None,
        pull_port=None,
        pull_path=pull_path,
        pull_path_perms=pull_path_perms,
    )
    assert pubserv.pull_path == pull_path
    assert pubserv.pull_path_perms == pull_path_perms
    assert pubserv.pull_host is None
    assert pubserv.pull_port is None
    with patch("os.chmod") as p:
        await pubserv.publisher(publish_payload)
        assert p.call_count == 1
        assert p.call_args.args == (pubserv.pull_path, pubserv.pull_path_perms)


@pytest.mark.skip_on_windows()
async def test_pub_server_publisher_pub_path_perms(master_opts, io_loop, tmp_path):
    def publish_payload(payload):
        return payload

    pub_path = str(tmp_path / "pub.ipc")
    pub_path_perms = 0o664
    pubserv = salt.transport.tcp.PublishServer(
        master_opts,
        pub_host=None,
        pub_port=None,
        pub_path=pub_path,
        pub_path_perms=pub_path_perms,
        pull_host="127.0.0.1",
        pull_port=5151,
        pull_path=None,
    )
    assert pubserv.pub_path == pub_path
    assert pubserv.pub_path_perms == pub_path_perms
    assert pubserv.pub_host is None
    assert pubserv.pub_port is None
    with patch("os.chmod") as p:
        await pubserv.publisher(publish_payload)
        assert p.call_count == 1
        assert p.call_args.args == (pubserv.pub_path, pubserv.pub_path_perms)
