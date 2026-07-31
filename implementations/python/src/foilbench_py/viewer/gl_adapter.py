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

from foilbench_py.viewer.app import ViewerModel, viewer_bounds
from foilbench_py.viewer.worker import SimulationWorker

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

_FIELD_VERTEX = """
#version 330
out vec2 uv;
const vec2 positions[4] = vec2[4](
    vec2(-1.0, -1.0),
    vec2( 1.0, -1.0),
    vec2(-1.0,  1.0),
    vec2( 1.0,  1.0)
);
void main() {
    vec2 position = positions[gl_VertexID];
    uv = 0.5 * (position + 1.0);
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

_FIELD_FRAGMENT = """
#version 330
in vec2 uv;
out vec4 fragColor;
uniform sampler2D vorticity_texture;
uniform vec2 uv_min;
uniform vec2 uv_max;
void main() {
    float value = texture(vorticity_texture, mix(uv_min, uv_max, uv)).r;
    float magnitude = pow(min(abs(value), 1.0), 0.7);
    float visibility = smoothstep(0.18, 0.9, magnitude);
    vec3 clockwise = vec3(0.65, 0.12, 0.02);
    vec3 counterclockwise = vec3(0.02, 0.28, 0.65);
    vec3 selected = value >= 0.0 ? clockwise : counterclockwise;
    vec3 background = vec3(0.015, 0.02, 0.035);
    fragColor = vec4(mix(background, selected, 0.38 * visibility), 1.0);
}
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
        self.worker = SimulationWorker(model)
        self.scenario = self.worker.scenario
        self.geometry = self.worker.geometry
        self.full_view_bounds = viewer_bounds(self.scenario, cropped=False)
        self.cropped_view_bounds = viewer_bounds(self.scenario, cropped=True)
        self.crop_available = self.cropped_view_bounds != self.full_view_bounds
        self.crop_enabled = self.crop_available
        self.view_bounds = (
            self.cropped_view_bounds if self.crop_enabled else self.full_view_bounds
        )
        self.snapshot = self.worker.latest_snapshot()
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.BLEND | moderngl.PROGRAM_POINT_SIZE)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.point_program = self.ctx.program(
            vertex_shader=_VERTEX_SHADER, fragment_shader=_POINT_FRAGMENT
        )
        self.line_program = self.ctx.program(
            vertex_shader=_VERTEX_SHADER, fragment_shader=_LINE_FRAGMENT
        )
        self.field_program = self.ctx.program(
            vertex_shader=_FIELD_VERTEX,
            fragment_shader=_FIELD_FRAGMENT,
        )
        point_bytes = self.snapshot.positions.shape[0] * 2 * 4
        line_vertices = self.worker.maximum_line_vertices
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
        self.field_vao = self.ctx.vertex_array(self.field_program, [])
        self.field_texture = self.ctx.texture(
            (self.scenario.domain.nx, self.scenario.domain.ny),
            components=1,
            dtype="f4",
        )
        self.field_texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.field_texture.repeat_x = False
        self.field_texture.repeat_y = False
        self._update_field_view()
        self.field_revision = -1
        self.label = pyglet.text.Label(
            "",
            x=12,
            y=self.height - 12,
            anchor_x="left",
            anchor_y="top",
            color=(235, 241, 255, 255),
            font_size=11,
        )
        self.help_label = pyglet.text.Label(
            "1/2/3 solver  drag foil  Space pause  R reset  -/+ Re  0 Re reset  "
            "[/] blend  V vort  T tracers  C crop",
            x=12,
            y=self.height - 32,
            anchor_x="left",
            anchor_y="top",
            color=(185, 198, 220, 255),
            font_size=9,
        )
        self.worker.start()
        pyglet.clock.schedule_interval(self._tick, 1.0 / 60.0)

    def _update_field_view(self) -> None:
        domain_x0, domain_x1 = self.full_view_bounds[0]
        domain_y0, domain_y1 = self.full_view_bounds[1]
        view_x0, view_x1 = self.view_bounds[0]
        view_y0, view_y1 = self.view_bounds[1]
        self.field_program["uv_min"].value = (
            (view_x0 - domain_x0) / (domain_x1 - domain_x0),
            (view_y0 - domain_y0) / (domain_y1 - domain_y0),
        )
        self.field_program["uv_max"].value = (
            (view_x1 - domain_x0) / (domain_x1 - domain_x0),
            (view_y1 - domain_y0) / (domain_y1 - domain_y0),
        )

    def _clip(self, points: np.ndarray) -> np.ndarray:
        x0, x1 = self.view_bounds[0]
        y0, y1 = self.view_bounds[1]
        clipped = np.empty_like(points, dtype=np.float32)
        clipped[:, 0] = 2.0 * (points[:, 0] - x0) / (x1 - x0) - 1.0
        clipped[:, 1] = 2.0 * (points[:, 1] - y0) / (y1 - y0) - 1.0
        return clipped

    def _tick(self, dt: float) -> None:
        del dt
        self.snapshot = self.worker.latest_snapshot()
        view_status = "cropped" if self.crop_enabled else "full"
        self.label.text = f"{self.snapshot.status}  view={view_status}"
        self.label.y = self.height - 12
        self.help_label.y = self.height - 32
        self.invalid = True

    def on_draw(self) -> None:
        self.ctx.viewport = (0, 0, self.width, self.height)
        self.ctx.clear(0.015, 0.02, 0.035, 1.0)
        if self.snapshot.show_vorticity and self.snapshot.vorticity is not None:
            if self.field_revision != self.snapshot.vorticity_revision:
                field = np.asarray(self.snapshot.vorticity, dtype=np.float32)
                self.field_texture.write(field.tobytes())
                self.field_revision = self.snapshot.vorticity_revision
            self.field_texture.use(location=0)
            self.field_program["vorticity_texture"].value = 0
            self.field_vao.render(mode=moderngl.TRIANGLE_STRIP, vertices=4)

        points = self._clip(self.snapshot.positions)
        self.point_buffer.write(points.tobytes())
        self.point_program["point_size"].value = 2.4
        self.point_program["color"].value = (0.48, 0.72, 1.0, 0.58)
        self.point_vao.render(mode=moderngl.POINTS, vertices=points.shape[0])

        segments = self._clip(self.snapshot.path_segments)
        if segments.size:
            self.line_buffer.write(segments.tobytes())
            self.line_program["point_size"].value = 1.0
            self.line_program["color"].value = (0.38, 0.64, 1.0, 0.035)
            self.line_vao.render(mode=moderngl.LINES, vertices=segments.shape[0])

        outline = self._clip(self.geometry.outline(self.snapshot.angle_degrees))
        self.foil_buffer.write(outline.tobytes())
        self.line_program["color"].value = (0.78, 0.86, 0.93, 1.0)
        self.foil_vao.render(mode=moderngl.LINE_LOOP, vertices=outline.shape[0])
        self.label.draw()
        self.help_label.draw()

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        del modifiers
        if symbol == key.SPACE:
            self.worker.toggle_pause()
        elif symbol == key.R:
            self.worker.reset()
        elif symbol in (key._1, key.NUM_1):
            self.worker.switch_solver("stable-fluids")
        elif symbol in (key._2, key.NUM_2):
            self.worker.switch_solver("lbm-d2q9")
        elif symbol in (key._3, key.NUM_3):
            self.worker.switch_solver("pic-flip")
        elif symbol == key.BRACKETLEFT:
            self.worker.adjust_blend(-0.05)
        elif symbol == key.BRACKETRIGHT:
            self.worker.adjust_blend(0.05)
        elif symbol in (key.MINUS, key.NUM_SUBTRACT):
            self.worker.adjust_reynolds(-0.25)
        elif symbol in (key.EQUAL, key.PLUS, key.NUM_ADD):
            self.worker.adjust_reynolds(0.25)
        elif symbol in (key._0, key.NUM_0):
            self.worker.reset_reynolds()
        elif symbol == key.V:
            self.worker.toggle_vorticity()
        elif symbol == key.T:
            self.worker.toggle_tracer_mode()
        elif symbol == key.C and self.crop_available:
            self.crop_enabled = not self.crop_enabled
            self.view_bounds = (
                self.cropped_view_bounds if self.crop_enabled else self.full_view_bounds
            )
            self._update_field_view()

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
        x0, x1 = self.view_bounds[0]
        y0, y1 = self.view_bounds[1]
        world_x = x0 + x / max(self.width, 1) * (x1 - x0)
        world_y = y0 + y / max(self.height, 1) * (y1 - y0)
        pivot = self.scenario.foil.pivot
        angle = math.degrees(math.atan2(world_y - pivot[1], world_x - pivot[0]))
        self.worker.set_angle(angle)

    def on_mouse_release(self, x: int, y: int, button: int, modifiers: int) -> None:
        del x, y, button, modifiers
        self.worker.release_angle()

    def on_close(self) -> None:
        pyglet.clock.unschedule(self._tick)
        self.worker.close()
        super().on_close()


def run_gl_window(model: ViewerModel) -> None:
    window = FoilWindow(model)
    try:
        pyglet.app.run()
    finally:
        window.worker.close()
