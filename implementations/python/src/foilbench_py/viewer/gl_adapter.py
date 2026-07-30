# pyright: reportImplicitOverride=false
# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportPrivateUsage=false
"""Deliberately narrow adapter around dynamic pyglet and ModernGL APIs."""

import math

import moderngl
import numpy as np
import pyglet
from pyglet.window import key, mouse

from foilbench_py.viewer.app import ViewerModel

_VERTEX_SHADER = """
#version 330
in vec2 in_position;
uniform float point_size;
void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    gl_PointSize = point_size;
}
"""

_POINT_FRAGMENT = """
#version 330
out vec4 fragColor;
uniform vec4 color;
void main() {
    vec2 centered = gl_PointCoord - vec2(0.5);
    if (dot(centered, centered) > 0.25) discard;
    fragColor = color;
}
"""

_LINE_FRAGMENT = """
#version 330
out vec4 fragColor;
uniform vec4 color;
void main() { fragColor = color; }
"""


class FoilWindow(pyglet.window.Window):
    def __init__(self, model: ViewerModel) -> None:
        super().__init__(
            width=1280,
            height=720,
            caption="FoilBench Python reference",
            resizable=True,
            vsync=True,
        )
        self.model = model
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.BLEND | moderngl.PROGRAM_POINT_SIZE)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.point_program = self.ctx.program(
            vertex_shader=_VERTEX_SHADER, fragment_shader=_POINT_FRAGMENT
        )
        self.line_program = self.ctx.program(
            vertex_shader=_VERTEX_SHADER, fragment_shader=_LINE_FRAGMENT
        )
        point_bytes = model.tracers.positions.shape[0] * 2 * 4
        line_vertices = (model.tracers.history.shape[0] - 1) * model.tracers.positions.shape[0] * 2
        self.point_buffer = self.ctx.buffer(reserve=point_bytes, dynamic=True)
        self.line_buffer = self.ctx.buffer(reserve=line_vertices * 2 * 4, dynamic=True)
        self.foil_buffer = self.ctx.buffer(reserve=1024 * 2 * 4, dynamic=True)
        self.point_vao = self.ctx.vertex_array(
            self.point_program, [(self.point_buffer, "2f", "in_position")]
        )
        self.line_vao = self.ctx.vertex_array(
            self.line_program, [(self.line_buffer, "2f", "in_position")]
        )
        self.foil_vao = self.ctx.vertex_array(
            self.line_program, [(self.foil_buffer, "2f", "in_position")]
        )
        self.label = pyglet.text.Label(
            "",
            x=12,
            y=self.height - 12,
            anchor_x="left",
            anchor_y="top",
            color=(235, 241, 255, 255),
            font_size=11,
        )
        pyglet.clock.schedule_interval(self._tick, 1.0 / 60.0)

    def _clip(self, points: np.ndarray) -> np.ndarray:
        x0, x1 = self.model.scenario.domain.bounds[0]
        y0, y1 = self.model.scenario.domain.bounds[1]
        clipped = np.empty_like(points, dtype=np.float32)
        clipped[:, 0] = 2.0 * (points[:, 0] - x0) / (x1 - x0) - 1.0
        clipped[:, 1] = 2.0 * (points[:, 1] - y0) / (y1 - y0) - 1.0
        return clipped

    def _tick(self, dt: float) -> None:
        self.model.update(dt)
        self.label.text = (
            self.model.status() + "   [1/2/3 switch, drag foil, Space pause, R reset, [/] blend]"
        )
        self.label.y = self.height - 12
        self.invalid = True

    def on_draw(self) -> None:
        self.ctx.viewport = (0, 0, self.width, self.height)
        self.ctx.clear(0.015, 0.02, 0.035, 1.0)
        points = self._clip(self.model.tracers.positions)
        self.point_buffer.write(points.tobytes())
        self.point_program["point_size"].value = 3.2
        self.point_program["color"].value = (0.24, 0.58, 1.0, 0.86)
        self.point_vao.render(mode=moderngl.POINTS, vertices=points.shape[0])

        segments = self._clip(self.model.tracers.path_segments())
        if segments.size:
            self.line_buffer.write(segments.tobytes())
            self.line_program["point_size"].value = 1.0
            self.line_program["color"].value = (0.22, 0.55, 1.0, 0.10)
            self.line_vao.render(mode=moderngl.LINES, vertices=segments.shape[0])

        control = self.model.control(self.model.scenario.output_dt)
        outline = self._clip(self.model.geometry.outline(control.angle_degrees))
        self.foil_buffer.write(outline.tobytes())
        self.line_program["color"].value = (0.78, 0.86, 0.93, 1.0)
        self.foil_vao.render(mode=moderngl.LINE_LOOP, vertices=outline.shape[0])
        self.label.draw()

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        del modifiers
        if symbol == key.SPACE:
            self.model.paused = not self.model.paused
        elif symbol == key.R:
            self.model.reset()
        elif symbol in (key._1, key.NUM_1):
            self.model.switch_solver("stable-fluids")
        elif symbol in (key._2, key.NUM_2):
            self.model.switch_solver("lbm-d2q9")
        elif symbol in (key._3, key.NUM_3):
            self.model.switch_solver("pic-flip")
        elif symbol == key.BRACKETLEFT:
            self.model.adjust_blend(-0.05)
        elif symbol == key.BRACKETRIGHT:
            self.model.adjust_blend(0.05)

    def on_mouse_drag(
        self,
        x: int,
        y: int,
        dx: int,
        dy: int,
        buttons: int,
        modifiers: int,
    ) -> None:
        del dx, dy, modifiers
        if not buttons & mouse.LEFT:
            return
        x0, x1 = self.model.scenario.domain.bounds[0]
        y0, y1 = self.model.scenario.domain.bounds[1]
        world_x = x0 + x / max(self.width, 1) * (x1 - x0)
        world_y = y0 + y / max(self.height, 1) * (y1 - y0)
        pivot = self.model.scenario.foil.pivot
        angle = math.degrees(math.atan2(world_y - pivot[1], world_x - pivot[0]))
        self.model.set_angle(angle)

    def on_mouse_release(self, x: int, y: int, button: int, modifiers: int) -> None:
        del x, y, button, modifiers
        self.model.release_angle()

    def on_close(self) -> None:
        pyglet.clock.unschedule(self._tick)
        super().on_close()


def run_gl_window(model: ViewerModel) -> None:
    FoilWindow(model)
    pyglet.app.run()
