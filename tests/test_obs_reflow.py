"""Tests for the OBS reflow layout (pure functions, no OBS needed)."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
spec = importlib.util.spec_from_file_location('obs_reflow', ROOT / 'server/obs_reflow.py')
reflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reflow)


def item(name, enabled, y=0.0, item_id=1):
    return {'sourceName': name, 'sceneItemEnabled': enabled, 'sceneItemId': item_id,
            'sceneItemTransform': {'positionY': y}}


class RowLayoutTests(unittest.TestCase):
    def test_nothing_visible_parks_everything(self):
        self.assertEqual(set(reflow.row_layout([]).values()), {reflow.PARK_Y})

    def test_one_visible_sits_at_the_top(self):
        layout = reflow.row_layout(['hermes'])
        self.assertEqual(layout['Token Chyron - Hermes'], reflow.TOP)
        self.assertEqual(layout['Token Chyron - Codex'], reflow.PARK_Y)

    def test_visible_chyrons_stack_from_the_top_in_order_without_gaps(self):
        layout = reflow.row_layout(['codex', 'claude', 'hermes'])
        step = reflow.ROW + reflow.GAP
        self.assertEqual([layout[reflow.SOURCE_NAMES[a]] for a in reflow.ORDER],
                         [reflow.TOP, reflow.TOP + step, reflow.TOP + 2 * step])

    def test_hiding_the_top_one_lifts_the_rest(self):
        layout = reflow.row_layout(['claude', 'hermes'])
        self.assertEqual(layout['Token Chyron - Claude'], reflow.TOP)
        self.assertEqual(layout['Token Chyron - Hermes'], reflow.TOP + reflow.ROW + reflow.GAP)
        self.assertEqual(layout['Token Chyron - Codex'], reflow.PARK_Y)

    def test_the_stack_stays_inside_a_1080p_canvas(self):
        layout = reflow.row_layout(['codex', 'claude', 'hermes'])
        bottom = max(layout.values()) + reflow.ROW
        self.assertLessEqual(bottom, 1080)
        self.assertGreaterEqual(min(reflow.row_layout([]).values()), -1080)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.service = reflow.Reflow(log=lambda *_: None)

    def test_plan_only_contains_items_that_are_out_of_place(self):
        step = reflow.ROW + reflow.GAP
        items = [
            item('Token Chyron - Codex', True, reflow.TOP, 1),
            item('Token Chyron - Claude', True, reflow.TOP + step, 2),          # already right
            item('Token Chyron - Hermes', True, reflow.PARK_Y, 3),              # needs to lift
        ]
        plan = self.service.plan(items)
        self.assertEqual(set(plan), {'Token Chyron - Hermes'})
        self.assertEqual(plan['Token Chyron - Hermes'], (3, reflow.TOP + 2 * step))

    def test_plan_parks_a_hidden_chyron(self):
        step = reflow.ROW + reflow.GAP
        items = [item('Token Chyron - Codex', False, reflow.TOP, 1),
                 item('Token Chyron - Claude', True, reflow.TOP + step, 2)]
        plan = self.service.plan(items)
        self.assertEqual(plan['Token Chyron - Codex'], (1, reflow.PARK_Y))
        # Claude is the only one left visible, so it lifts into the top row.
        self.assertEqual(plan['Token Chyron - Claude'], (2, reflow.TOP))

    def test_a_sole_visible_chyron_already_at_the_top_is_left_alone(self):
        items = [item('Token Chyron - Codex', False, reflow.TOP, 1),
                 item('Token Chyron - Claude', True, reflow.TOP, 2)]
        self.assertEqual(self.service.plan(items), {'Token Chyron - Codex': (1, reflow.PARK_Y)})

    def test_plan_ignores_sources_that_are_not_chyrons(self):
        items = [item('Confetti', True, 999, 1), item('Token Chyron - Codex', True, 0, 2)]
        plan = self.service.plan(items)
        self.assertEqual(set(plan), {'Token Chyron - Codex'})

    def test_a_settled_layout_produces_no_moves(self):
        items = [item(reflow.SOURCE_NAMES[a], True, reflow.TOP + i * (reflow.ROW + reflow.GAP), i + 1)
                 for i, a in enumerate(reflow.ORDER)]
        self.assertEqual(self.service.plan(items), {})


class FakeClient:
    """Just enough of obsws-python's ReqClient for apply()."""

    def __init__(self, items, on_air=True):
        self.items = items
        self.on_air = on_air
        self.writes = []

    def get_scene_item_list(self, scene):
        return SimpleNamespace(scene_items=self.items)

    def get_source_active(self, scene):
        return SimpleNamespace(video_showing=self.on_air)

    def set_scene_item_transform(self, scene, item_id, transform):
        self.writes.append((item_id, transform))


class NoOnAirClient(FakeClient):
    """An OBS that cannot answer: the audio must stay audible, not go silent."""

    def get_source_active(self, scene):
        raise RuntimeError('GetSourceActive is not supported')


class VisibilityTests(unittest.TestCase):
    """The audio side reads `visibility`: what is on screen, and is it on air."""

    def setUp(self):
        self.service = reflow.Reflow(log=lambda *_: None)

    def test_visibility_names_every_chyron_even_the_hidden_ones(self):
        items = [item('Token Chyron - Codex', False, reflow.PARK_Y, 1),
                 item('Token Chyron - Claude', True, reflow.TOP, 2),
                 item('Token Chyron - Hermes', True, reflow.TOP + reflow.ROW + reflow.GAP, 3)]
        self.service.apply(FakeClient(items))
        self.assertEqual(self.service.visibility,
                         {'on_air': True,
                          'agents': {'codex': False, 'claude': True, 'hermes': True}})

    def test_an_off_air_scene_is_reported_even_when_nothing_moves(self):
        # Regression: apply() used to return before touching visibility, so a
        # settled layout left a stale "on air" behind and the tally kept firing.
        step = reflow.ROW + reflow.GAP
        items = [item(reflow.SOURCE_NAMES[a], True, reflow.TOP + i * step, i + 1)
                 for i, a in enumerate(reflow.ORDER)]
        client = FakeClient(items, on_air=False)
        self.assertEqual(self.service.apply(client), 0)
        self.assertEqual(client.writes, [])
        self.assertFalse(self.service.visibility['on_air'])

    def test_positions_are_cached_for_diagnostics(self):
        items = [item('Token Chyron - Codex', True, reflow.PARK_Y, 1)]
        self.service.apply(FakeClient(items))
        self.assertEqual(self.service.positions, {'Token Chyron - Codex': reflow.PARK_Y})

    def test_an_obs_that_cannot_answer_leaves_the_tally_audible(self):
        self.service.apply(NoOnAirClient([item('Token Chyron - Codex', True, reflow.TOP, 1)]))
        self.assertTrue(self.service.visibility['on_air'])

    def test_status_carries_visibility_to_health_and_the_payload(self):
        self.service.apply(FakeClient([item('Token Chyron - Claude', True, reflow.TOP, 1)],
                                      on_air=False))
        status = self.service.status()
        self.assertFalse(status['visibility']['on_air'])
        self.assertEqual(status['visibility']['agents'],
                         {'codex': False, 'claude': True, 'hermes': False})


class LoggingMustNotKillTheLoopTests(unittest.TestCase):
    """A logger that fails must not be able to stop the layout service.

    Regression: the server was launched from a terminal that has since closed, so
    its stdout is a pipe with no reader and ``print`` raises BrokenPipeError. The
    move-logging call let that escape ``run()`` and killed the reflow thread:
    /api/health froze on ``state: error``, and a chyron the deck key switched on
    afterwards stayed parked at y=-400 - on the deck it looked like a key that
    did nothing, and on stream the banner never appeared.
    """

    def test_the_default_logger_survives_a_dead_stdout(self):
        class DeadPipe(io.TextIOBase):
            def write(self, _text):
                raise BrokenPipeError(32, 'Broken pipe')

            def flush(self):
                raise BrokenPipeError(32, 'Broken pipe')

        with contextlib.redirect_stdout(DeadPipe()):
            reflow._safe_log('chyron reflow: Codex->40')  # must not raise

    def test_a_logger_that_raises_does_not_escape_run(self):
        calls = []
        service = reflow.Reflow(log=lambda *_: (_ for _ in ()).throw(BrokenPipeError(32, 'Broken pipe')),
                                retry_seconds=0.01)

        def serve():
            calls.append(1)
            if len(calls) >= 3:
                service.stop.set()
            raise RuntimeError('OBS went away')

        service.serve = serve
        service.run()  # returns only if the loop survived three failures
        self.assertEqual(len(calls), 3)
        self.assertEqual(service.state, 'error')
        self.assertIn('OBS went away', service.error)

    def test_a_flaky_socket_does_not_stop_the_reconnect_loop(self):
        calls = []
        service = reflow.Reflow(log=lambda *_: None, retry_seconds=0.01)

        def serve():
            calls.append(1)
            if len(calls) >= 2:
                service.stop.set()
                return
            raise ConnectionResetError('socket closed')

        service.serve = serve
        service.run()
        self.assertEqual(len(calls), 2)

    def test_the_move_log_cannot_take_down_apply(self):
        service = reflow.Reflow(log=lambda *_: (_ for _ in ()).throw(BrokenPipeError(32, 'Broken pipe')))
        items = [item('Token Chyron - Codex', True, reflow.PARK_Y, 1)]
        self.assertEqual(service.apply(FakeClient(items)), 1)
        self.assertEqual(service.moves, 1)


class VisibilitySnapshotTests(unittest.TestCase):
    """The tally's audio gate when nothing is subscribed to OBS events.

    With the layout owned by an OBS macro (Advanced Scene Switcher) this process
    has no event subscription, so visibility is polled instead. The rule that
    matters does not change: unknown means audible.
    """

    class FakeClient:
        def __init__(self, items, on_air=True, fail=False):
            self.items, self.on_air, self.fail = items, on_air, fail

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_scene_item_list(self, scene):
            if self.fail:
                raise ConnectionResetError('socket closed')
            return SimpleNamespace(scene_items=self.items)

        def get_source_active(self, scene):
            return SimpleNamespace(video_showing=self.on_air)

    def factory(self, **kwargs):
        client = self.FakeClient(**kwargs)
        return lambda config: client

    def test_reports_each_chyron_and_air_state(self):
        items = [item('Token Chyron - Codex', True, reflow.TOP, 1),
                 item('Token Chyron - Claude', False, reflow.PARK_Y, 2),
                 item('Token Chyron - Hermes', True, reflow.TOP + reflow.ROW + reflow.GAP, 3)]
        snap = reflow.visibility_snapshot({'host': 'x'}, client_factory=self.factory(items=items, on_air=False))
        self.assertFalse(snap['on_air'])
        self.assertEqual(snap['agents'], {'codex': True, 'claude': False, 'hermes': True})

    def test_a_broken_obs_leaves_the_tally_audible(self):
        snap = reflow.visibility_snapshot({'host': 'x'}, client_factory=self.factory(items=[], fail=True))
        self.assertTrue(snap['on_air'])
        self.assertEqual(snap['agents'], {a: True for a in reflow.ORDER})

    def test_no_config_means_audible(self):
        snap = reflow.visibility_snapshot(None, client_factory=self.factory(items=[]))
        self.assertEqual(snap['agents'], {a: True for a in reflow.ORDER})

    def test_an_obs_without_get_source_active_still_reports_agents(self):
        class NoOnAir(self.FakeClient):
            def get_source_active(self, scene):
                raise RuntimeError('GetSourceActive is not supported')

        client = NoOnAir([item('Token Chyron - Codex', False, reflow.PARK_Y, 1)])
        snap = reflow.visibility_snapshot({'host': 'x'}, client_factory=lambda config: client)
        self.assertTrue(snap['on_air'])
        self.assertFalse(snap['agents']['codex'])


if __name__ == '__main__':
    unittest.main()
