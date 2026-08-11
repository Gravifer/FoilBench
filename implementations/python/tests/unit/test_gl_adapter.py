from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from foilbench_py.viewer.gl_adapter import FoilWindow
from foilbench_py.viewer.worker import ViewerSnapshot


class _SnapshotWorker:
    def __init__(self, snapshot: ViewerSnapshot) -> None:
        self.snapshot = snapshot

    def latest_snapshot(self) -> ViewerSnapshot:
        return self.snapshot


def _snapshot(revision: int, status: str, *, cropped: bool) -> ViewerSnapshot:
    return ViewerSnapshot(
        revision=revision,
        applied_command=0,
        solver_epoch=0,
        solver_state_revision=0,
        diagnostic_solver_state_revision=None,
        vorticity_solver_state_revision=None,
        simulation_time=float(revision),
        angle_degrees=0.0,
        status=status,
        positions=np.zeros((1, 2), dtype=np.float64),
        path_segments=np.zeros((0, 2), dtype=np.float64),
        vorticity=None,
        vorticity_revision=0,
        show_vorticity=False,
        crop_enabled=cropped,
        phase="running",
        motion_mode="resolved",
        diagnostic_mode="cadenced",
        schedule_active=True,
        recovery_epoch=0,
    )


def test_tick_consumes_latest_non_consuming_worker_snapshot() -> None:
    initial = _snapshot(0, "initial", cropped=False)
    latest = _snapshot(3, "advanced", cropped=True)
    label = SimpleNamespace(text="", y=0)
    help_label = SimpleNamespace(y=0)
    updates: list[bool] = []
    window = SimpleNamespace(
        worker=_SnapshotWorker(latest),
        snapshot=initial,
        crop_enabled=False,
        cropped_view_bounds=((1.0, 2.0), (1.0, 2.0)),
        full_view_bounds=((0.0, 3.0), (0.0, 3.0)),
        view_bounds=((0.0, 3.0), (0.0, 3.0)),
        label=label,
        help_label=help_label,
        height=720,
        invalid=False,
        _update_field_view=lambda: updates.append(True),
    )

    FoilWindow._tick(cast(Any, window), 1.0 / 60.0)

    assert window.snapshot is latest
    assert window.crop_enabled
    assert window.view_bounds == window.cropped_view_bounds
    assert updates == [True]
    assert label.text == "advanced"
    assert window.invalid


def test_tick_does_not_redraw_an_unchanged_snapshot() -> None:
    snapshot = _snapshot(2, "paused", cropped=False)
    window = SimpleNamespace(
        worker=_SnapshotWorker(snapshot),
        snapshot=snapshot,
        crop_enabled=False,
        cropped_view_bounds=((1.0, 2.0), (1.0, 2.0)),
        full_view_bounds=((0.0, 3.0), (0.0, 3.0)),
        view_bounds=((0.0, 3.0), (0.0, 3.0)),
        label=SimpleNamespace(text="paused", y=0),
        help_label=SimpleNamespace(y=0),
        height=720,
        invalid=False,
        _update_field_view=lambda: None,
    )

    FoilWindow._tick(cast(Any, window), 1.0 / 60.0)

    assert not window.invalid
