import datetime
import time

import pytest

from . import normalize_ret

pytestmark = [
    pytest.mark.windows_whitelisted,
    pytest.mark.core_test,
]


def test_requisites_full_sls_require(state, state_tree):
    """
    Test the sls special command in requisites
    """
    full_sls_contents = """
    B:
      cmd.run:
        - name: echo B
    C:
      cmd.run:
        - name: echo C
    """
    sls_contents = """
    include:
      - fullsls
    A:
      cmd.run:
        - name: echo A
        - require:
          - sls: fullsls
    """
    expected_result = {
        "cmd_|-A_|-echo A_|-run": {
            "__run_num__": 2,
            "comment": 'Command "echo A" run',
            "result": True,
            "changes": True,
        },
        "cmd_|-B_|-echo B_|-run": {
            "__run_num__": 0,
            "comment": 'Command "echo B" run',
            "result": True,
            "changes": True,
        },
        "cmd_|-C_|-echo C_|-run": {
            "__run_num__": 1,
            "comment": 'Command "echo C" run',
            "result": True,
            "changes": True,
        },
    }
    with pytest.helpers.temp_file(
        "requisite.sls", sls_contents, state_tree
    ), pytest.helpers.temp_file("fullsls.sls", full_sls_contents, state_tree):
        ret = state.sls("requisite")
        result = normalize_ret(ret.raw)
        assert result == expected_result


def test_requisites_require_no_state_module(state, state_tree):
    """
    Call sls file containing several require_in and require with a missing req.

    Ensure an error is given.
    """
    sls_contents = """
    # Complex require/require_in graph
    #
    # Relative order of C>E is given by the definition order
    #
    # D (1) <--+
    #          |
    # B (2) ---+ <-+ <-+ <-+
    #              |   |   |
    # C (3) <--+ --|---|---+
    #          |   |   |
    # E (4) ---|---|---+ <-+
    #          |   |       |
    # A (5) ---+ --+ ------+
    #

    A:
      cmd.run:
        - name: echo A fifth
        - require:
          - C
    B:
      cmd.run:
        - name: echo B second
        - require_in:
          - A
          - C

    C:
      cmd.run:
        - name: echo C third

    D:
      cmd.run:
        - name: echo D first
        - require_in:
          - B

    E:
      cmd.run:
        - name: echo E fourth
        - require:
          - B
        - require_in:
          - A

    # will fail with "Referenced state does not exist for requisite"
    G:
      cmd.run:
        - name: echo G
        - require:
          - Z
    # will fail with "Referenced state does not exist for requisite"
    H:
      cmd.run:
        - name: echo H
        - require:
          - Z
    """
    errmsgs = [
        (
            "Referenced state does not exist for requisite [require: (id: Z)]"
            " in state [echo G] in SLS [requisite]"
        ),
        (
            "Referenced state does not exist for requisite [require: (id: Z)]"
            " in state [echo H] in SLS [requisite]"
        ),
    ]

    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == errmsgs


def test_requisites_require_ordering_and_errors_1(state, state_tree):
    """
    Call sls file containing several require_in and require.

    Ensure there are errors due to requisites.
    """
    sls_contents = """
    # Complex require/require_in graph
    #
    # Relative order of C>E is given by the definition order
    #
    # D (1) <--+
    #          |
    # B (2) ---+ <-+ <-+ <-+
    #              |   |   |
    # C (3) <--+ --|---|---+
    #          |   |   |
    # E (4) ---|---|---+ <-+
    #          |   |       |
    # A (5) ---+ --+ ------+
    #

    A:
      cmd.run:
        - name: echo A fifth
        - require:
          - cmd: C
    B:
      cmd.run:
        - name: echo B second
        - require_in:
          - cmd: A
          - cmd: C

    C:
      cmd.run:
        - name: echo C third

    D:
      cmd.run:
        - name: echo D first
        - require_in:
          - cmd: B

    E:
      cmd.run:
        - name: echo E fourth
        - require:
          - cmd: B
        - require_in:
          - cmd: A

    # will fail with "Referenced state does not exist for requisite"
    F:
      cmd.run:
        - name: echo F
        - require:
          - foobar: A
    # will fail with "Referenced state does not exist for requisite"
    G:
      cmd.run:
        - name: echo G
        - require:
          - cmd: Z
    # will fail with "Referenced state does not exist for requisite"
    H:
      cmd.run:
        - name: echo H
        - require:
          - cmd: Z
    """
    errmsgs = [
        (
            "Referenced state does not exist for requisite [require: (foobar: A)]"
            " in state [echo F] in SLS [requisite]"
        ),
        (
            "Referenced state does not exist for requisite [require: (cmd: Z)]"
            " in state [echo G] in SLS [requisite]"
        ),
        (
            "Referenced state does not exist for requisite [require: (cmd: Z)]"
            " in state [echo H] in SLS [requisite]"
        ),
    ]

    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == errmsgs


def test_requisites_require_ordering_and_errors_2(state, state_tree):
    """
    Call sls file containing several require_in and require.

    Ensure that some of them are failing and that the order is right.
    """
    sls_contents = """
    # will fail with "Data failed to compile:"
    A:
      cmd.run:
        - name: echo A
        - require_in:
          - foobar: W
    """
    errmsg = (
        "Cannot extend ID 'W' in 'base:requisite'. It is not part of the high"
        " state.\nThis is likely due to a missing include statement or an incorrectly"
        " typed ID.\nEnsure that a state with an ID of 'W' is available\nin environment"
        " 'base' and to SLS 'requisite'"
    )
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == [errmsg]


def test_requisites_require_ordering_and_errors_3(state, state_tree):
    """
    Call sls file containing several require_in and require.

    Ensure that some of them are failing and that the order is right.
    """
    sls_contents = """
    # issue #8772
    # should fail with "Data failed to compile:"
    B:
      cmd.run:
        - name: echo B last
        - require_in:
          # state foobar does not exists in A
          - foobar: A

    A:
      cmd.run:
        - name: echo A first
    """
    errmsg = (
        "Cannot extend ID 'A' in 'base:requisite'. It is not part of the high state.\n"
        "This is likely due to a missing include statement or an incorrectly typed ID.\n"
        "Ensure that a state with an ID of 'A' is available\n"
        "in environment 'base' and to SLS 'requisite'"
    )
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == [errmsg]


def test_requisites_require_ordering_and_errors_4(state, state_tree):
    """
    Call sls file containing several require_in and require.

    Ensure that some of them are failing and that the order is right.
    """
    sls_contents = """
    A:
      cmd.run:
        - name: echo A
    B:
      cmd.run:
        - name: echo B
        # here used without "-"
        - require:
            cmd: A
    C:
      cmd.run:
        - name: echo C
        # here used without "-"
        - require_in:
            cmd: A
    """
    # issue #8235
    # FIXME: Why is require enforcing list syntax while require_in does not?
    # And why preventing it?
    # Currently this state fails, should return C/B/A
    errmsg = (
        "The require statement in state 'B' in SLS 'requisite' needs to be formed as a"
        " list"
    )
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == [errmsg]


def test_requisites_require_ordering_and_errors_5(state, state_tree):
    """
    Call sls file containing several require_in and require.

    Ensure that some of them are failing and that the order is right.
    """
    sls_contents = """
    A:
      cmd.run:
        - name: echo A
        - require:
          - cmd: B

    B:
      cmd.run:
        - name: echo B
        - require:
          - cmd: A
    """
    errmsg = (
        "Recursive requisites were found: "
        "({'SLS': 'requisite', 'ID': 'B', 'NAME': 'echo B'}, "
        "'require', {'SLS': 'requisite', 'ID': 'A', 'NAME': 'echo A'}), "
        "({'SLS': 'requisite', 'ID': 'A', 'NAME': 'echo A'}, 'require', "
        "{'SLS': 'requisite', 'ID': 'B', 'NAME': 'echo B'})"
    )
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert ret.failed
        assert ret.errors == [errmsg]


def test_requisites_require_with_order_first_last(state, state_tree):
    """
    Call sls file containing a state with require_in order first
    and require and order last.

    Ensure that the order is right.
    """
    sls_contents = """
    # Complex require/require_in graph
    #
    # Relative order of A > D is given by the definition order
    #
    # B (1) <------+
    #              |
    # A (2)        +
    #              |
    # D (3) <--+ --|
    #          |
    # E (4)    |
    #          |
    # C (5) ---+
    #

    A:
      test.succeed_with_changes

    B:
      test.succeed_with_changes:
        - order: first
        - require_in:
          - D

    C:
      test.succeed_with_changes:
        - order: last
        - require:
          - D

    D:
      test.succeed_with_changes

    E:
      test.succeed_with_changes
    """
    expected_result = {
        "test_|-B_|-B_|-succeed_with_changes": {
            "__run_num__": 0,
            "result": True,
            "changes": True,
            "comment": "Success!",
        },
        "test_|-A_|-A_|-succeed_with_changes": {
            "__run_num__": 1,
            "result": True,
            "changes": True,
            "comment": "Success!",
        },
        "test_|-D_|-D_|-succeed_with_changes": {
            "__run_num__": 2,
            "result": True,
            "changes": True,
            "comment": "Success!",
        },
        "test_|-E_|-E_|-succeed_with_changes": {
            "__run_num__": 3,
            "result": True,
            "changes": True,
            "comment": "Success!",
        },
        "test_|-C_|-C_|-succeed_with_changes": {
            "__run_num__": 4,
            "result": True,
            "changes": True,
            "comment": "Success!",
        },
    }
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        result = normalize_ret(ret.raw)
        assert result == expected_result


def test_requisites_require_any(state, state_tree):
    """
    Call sls file containing require_any.

    Ensure that the order is right.
    """
    sls_contents = """
    # Complex require/require_in graph
    #
    # Relative order of C>E is given by the definition order
    #
    # D (1) <--+
    #          |
    # B (2) ---+ <-+ <-+ <-+
    #              |   |   |
    # C (3) <--+ --|---|---+
    #          |   |   |
    # E (4) ---|---|---+ <-+
    #          |   |       |
    # A (5) ---+ --+ ------+
    #

    # A should success since B succeeds even though C fails.
    A:
      cmd.run:
        - name: echo A
        - require_any:
          - cmd: B
          - cmd: C
          - cmd: D
    B:
      cmd.run:
        - name: echo B

    C:
      cmd.run:
        - name: "$(which false)"

    D:
      cmd.run:
        - name: echo D
    """
    expected_result = {
        "cmd_|-A_|-echo A_|-run": {
            "__run_num__": 3,
            "comment": 'Command "echo A" run',
            "result": True,
            "changes": True,
        },
        "cmd_|-B_|-echo B_|-run": {
            "__run_num__": 0,
            "comment": 'Command "echo B" run',
            "result": True,
            "changes": True,
        },
        "cmd_|-C_|-$(which false)_|-run": {
            "__run_num__": 1,
            "comment": 'Command "$(which false)" run',
            "result": False,
            "changes": True,
        },
        "cmd_|-D_|-echo D_|-run": {
            "__run_num__": 2,
            "comment": 'Command "echo D" run',
            "result": True,
            "changes": True,
        },
    }
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        result = normalize_ret(ret.raw)
        assert result == expected_result


def test_requisites_require_any_fail(state, state_tree):
    """
    Call sls file containing require_any.

    Ensure that the order is right.
    """
    sls_contents = """
    # D should fail since both E & F fail
    E:
      cmd.run:
        - name: 'false'

    F:
      cmd.run:
        - name: 'false'

    D:
      cmd.run:
        - name: echo D
        - require_any:
          - cmd: E
          - cmd: F
    """
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert "One or more requisite failed" in ret["cmd_|-D_|-echo D_|-run"].comment


def test_issue_38683_require_order_failhard_combination(state, state_tree):
    """
    This tests the case where require, order, and failhard are all used together in a state definition.

    Previously, the order option, which used in tandem with require and failhard, would cause the state
    compiler to stacktrace. This exposed a logic error in the ``check_failhard`` function of the state
    compiler. With the logic error resolved, this test should now pass.

    See https://github.com/saltstack/salt/issues/38683 for more information.
    """
    sls_contents = """
    a:
      test.show_notification:
        - name: a
        - text: message
        - require:
            - test: b
        - order: 1
        - failhard: True

    b:
      test.fail_with_changes:
        - name: b
        - failhard: True
    """
    state_id = "test_|-b_|-b_|-fail_with_changes"
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        assert state_id in ret
        assert ret[state_id].result is False
        assert ret[state_id].comment == "Failure!"


@pytest.mark.skip_on_windows
def test_parallel_state_with_requires(state, state_tree):
    """
    This is a test case for https://github.com/saltstack/salt/issues/49273
    Parallel state object has any requisites
    """
    sls_contents = """
    barrier:
      cmd.run:
        - name: sleep 1

    {%- for x in range(1, 10) %}
    blah-{{x}}:
      cmd.run:
        - name: sleep 2
        - require:
          - barrier
          - barrier2
        - parallel: true
    {% endfor %}

    barrier2:
      test.nop
    """
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        start_time = time.time()
        ret = state.sls(
            "requisite",
            __pub_jid="1",  # Because these run in parallel we need a fake JID)
        )
        end_time = time.time()

        # We're running 3 states that sleep for 10 seconds each
        # they'll run in parallel so we should be below 30 seconds
        # confirm that the total runtime is below 30s
        assert (end_time - start_time) < 30

        for item in range(1, 10):
            _id = f"cmd_|-blah-{item}_|-sleep 2_|-run"
            assert "__parallel__" in ret[_id]


@pytest.mark.skip_on_windows
def test_parallel_state_with_requires_on_parallel(state, state_tree):
    """
    Parallel states requiring other parallel states should not block
    state execution while waiting on their requisites.

    Issue #59959
    """
    sls_contents = """
        service_a:
          cmd.run:
              - name: sleep 2
              - parallel: True

        service_b1:
          cmd.run:
              - name: sleep 5
              - parallel: True
              - require:
                  - service_a

        service_b2:
          cmd.run:
              - name: 'true'
              - parallel: True
              - require:
                  - service_b1

        service_c:
          cmd.run:
              - name: 'true'
              - parallel: True
              - require:
                  - service_a
    """

    with pytest.helpers.temp_file("requisite_parallel.sls", sls_contents, state_tree):
        ret = state.sls(
            "requisite_parallel",
            __pub_jid="1",  # Because these run in parallel we need a fake JID)
        )
        start_b1 = datetime.datetime.combine(
            datetime.date.today(),
            datetime.time.fromisoformat(
                ret["cmd_|-service_b1_|-sleep 5_|-run"]["start_time"]
            ),
        )
        start_c = datetime.datetime.combine(
            datetime.date.today(),
            datetime.time.fromisoformat(
                ret["cmd_|-service_c_|-true_|-run"]["start_time"]
            ),
        )
        start_diff = start_c - start_b1
        # Expected order:
        #   a > (b1, c) > b2
        # When b2 blocks while waiting for b1, c has to wait for b1 as well.
        # c should approximately start at the same time as b1 though.
        assert start_diff < datetime.timedelta(seconds=5)  # b1 sleeps for 5 seconds
        for state_ret in ret.raw.values():
            assert "__parallel__" in state_ret


@pytest.mark.skip_on_windows
def test_regular_state_requires_parallel(state, state_tree, tmp_path):
    """
    Regular states requiring parallel states should block until all
    requisites are executed.
    """
    tmpfile = tmp_path / "foo"
    sls_contents = f"""
        service_a:
          cmd.run:
              - name: sleep 3
              - parallel: True

        service_b:
          cmd.run:
              - name: 'touch {tmpfile}'
              - parallel: True
              - require:
                  - service_a

        service_c:
          cmd.run:
              - name: 'test -f {tmpfile}'
              - require:
                  - service_b
    """

    with pytest.helpers.temp_file("requisite_parallel_2.sls", sls_contents, state_tree):
        ret = state.sls(
            "requisite_parallel_2",
            __pub_jid="1",  # Because these run in parallel we need a fake JID)
        )
        assert ret[f"cmd_|-service_c_|-test -f {tmpfile}_|-run"]["result"] is True


def test_issue_59922_conflict_in_name_and_id_for_require_in(state, state_tree):
    """
    Make sure that state_type is always honored while compiling down require_in to

    corresponding require statement.
    """
    sls_contents = """
    X:
      test.succeed_without_changes:
        - name: A

    A:
      cmd.run:
        - name: echo A

    B:
      cmd.run:
        - name: echo B
        - require_in:
          - test: A
    """
    expected_result = {
        "cmd_|-A_|-echo A_|-run": {
            "__run_num__": 2,
            "comment": 'Command "echo A" run',
            "result": True,
            "changes": True,
        },
        "test_|-X_|-A_|-succeed_without_changes": {
            "__run_num__": 1,
            "comment": "Success!",
            "result": True,
            "changes": False,
        },
        "cmd_|-B_|-echo B_|-run": {
            "__run_num__": 0,
            "comment": 'Command "echo B" run',
            "result": True,
            "changes": True,
        },
    }
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        result = normalize_ret(ret.raw)
        assert result == expected_result


def test_issue_61121_extend_is_to_strict(state, state_tree):
    """
    test that extend works as advertised with adding new service_types to
    a state id
    """

    sls_contents = """
    A:
      test.succeed_without_changes:
        - name: a
    extend:
      A:
        cmd:
          - run
          - name: echo A
    """
    expected_result = {
        "test_|-A_|-a_|-succeed_without_changes": {
            "__run_num__": 0,
            "changes": False,
            "result": True,
            "comment": "Success!",
        },
        "cmd_|-A_|-echo A_|-run": {
            "__run_num__": 1,
            "changes": True,
            "result": True,
            "comment": 'Command "echo A" run',
        },
    }
    with pytest.helpers.temp_file("requisite.sls", sls_contents, state_tree):
        ret = state.sls("requisite")
        result = normalize_ret(ret.raw)
        assert result == expected_result
