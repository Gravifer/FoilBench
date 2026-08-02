# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportCallIssue=false
# pyright: reportMissingTypeStubs=false
"""Narrow adapter around SciPy's incompletely typed iterative solver."""

import numpy as np
from scipy.fft import dstn, idstn
from scipy.sparse.linalg import LinearOperator, cg

from foilbench_py.core.models import NumericalFailure


def solve_masked_poisson(
    rhs: np.ndarray,
    fluid: np.ndarray,
    dx: float,
    dy: float,
    periodic_axes: tuple[str, ...] = (),
    tolerance: float = 1.0e-5,
    max_iterations: int = 120,
) -> tuple[np.ndarray, int]:
    """Solve a masked negative Laplacian with solid-wall Neumann conditions."""

    ny, nx = rhs.shape
    inv_dx2 = 1.0 / (dx * dx)
    inv_dy2 = 1.0 / (dy * dy)

    diagonal = np.zeros((ny, nx), dtype=rhs.dtype)
    horizontal_edges = fluid[:, :-1] & fluid[:, 1:]
    vertical_edges = fluid[:-1, :] & fluid[1:, :]
    wrap_x = np.zeros(ny, dtype=np.bool_)
    wrap_y = np.zeros(nx, dtype=np.bool_)
    diagonal[:, :-1] += horizontal_edges * inv_dx2
    diagonal[:, 1:] += horizontal_edges * inv_dx2
    diagonal[:-1, :] += vertical_edges * inv_dy2
    diagonal[1:, :] += vertical_edges * inv_dy2
    if "x" in periodic_axes:
        wrap_x = fluid[:, -1] & fluid[:, 0]
        diagonal[:, -1] += wrap_x * inv_dx2
        diagonal[:, 0] += wrap_x * inv_dx2
    else:
        diagonal[:, 0] += fluid[:, 0] * inv_dx2
        diagonal[:, -1] += fluid[:, -1] * inv_dx2
    if "y" in periodic_axes:
        wrap_y = fluid[-1, :] & fluid[0, :]
        diagonal[-1, :] += wrap_y * inv_dy2
        diagonal[0, :] += wrap_y * inv_dy2
    else:
        diagonal[0, :] += fluid[0, :] * inv_dy2
        diagonal[-1, :] += fluid[-1, :] * inv_dy2
    diagonal[~fluid] = 1.0

    def apply(flat: np.ndarray) -> np.ndarray:
        pressure = flat.reshape(ny, nx)
        result = diagonal * pressure
        result[:, :-1] -= pressure[:, 1:] * horizontal_edges * inv_dx2
        result[:, 1:] -= pressure[:, :-1] * horizontal_edges * inv_dx2
        result[:-1, :] -= pressure[1:, :] * vertical_edges * inv_dy2
        result[1:, :] -= pressure[:-1, :] * vertical_edges * inv_dy2
        if "x" in periodic_axes:
            result[:, -1] -= pressure[:, 0] * wrap_x * inv_dx2
            result[:, 0] -= pressure[:, -1] * wrap_x * inv_dx2
        if "y" in periodic_axes:
            result[-1, :] -= pressure[0, :] * wrap_y * inv_dy2
            result[0, :] -= pressure[-1, :] * wrap_y * inv_dy2
        result[~fluid] = pressure[~fluid]
        return result.ravel()

    size = nx * ny
    operator = LinearOperator((size, size), matvec=apply, dtype=rhs.dtype)
    inverse_diagonal = 1.0 / np.maximum(diagonal, np.finfo(rhs.dtype).eps)

    if not periodic_axes:
        mode_x = np.arange(1, nx + 1, dtype=rhs.dtype)
        mode_y = np.arange(1, ny + 1, dtype=rhs.dtype)
        eigen_x = 2.0 * (1.0 - np.cos(np.pi * mode_x / (nx + 1))) * inv_dx2
        eigen_y = 2.0 * (1.0 - np.cos(np.pi * mode_y / (ny + 1))) * inv_dy2
        inverse_eigenvalues = 1.0 / (eigen_y[:, None] + eigen_x[None, :])

        def apply_preconditioner(flat: np.ndarray) -> np.ndarray:
            residual = flat.reshape(ny, nx)
            fluid_residual = np.where(fluid, residual, 0.0)
            transformed = dstn(fluid_residual, type=1, norm="ortho")
            approximate = np.asarray(
                idstn(
                    transformed * inverse_eigenvalues,
                    type=1,
                    norm="ortho",
                ),
                dtype=rhs.dtype,
            )
            approximate[~fluid] = residual[~fluid]
            return approximate.ravel()

    else:

        def apply_preconditioner(flat: np.ndarray) -> np.ndarray:
            return inverse_diagonal.ravel() * flat

    preconditioner = LinearOperator(
        (size, size),
        matvec=apply_preconditioner,
        dtype=rhs.dtype,
    )
    compatible_rhs = rhs.copy()
    singular = "x" in periodic_axes and "y" in periodic_axes
    if singular and np.any(fluid):
        compatible_rhs[fluid] -= np.mean(compatible_rhs[fluid])
    compatible_rhs[~fluid] = 0.0
    if not np.isfinite(compatible_rhs).all():
        raise NumericalFailure("nonfinite_state", "pressure RHS contains non-finite values")
    safe_component = 0.25 * np.sqrt(np.finfo(rhs.dtype).max / max(size, 1))
    if float(np.max(np.abs(compatible_rhs))) > safe_component:
        raise NumericalFailure(
            "projection_failure",
            "pressure RHS exceeds the safe CG dot-product range",
        )

    def validate_iterate(iterate: np.ndarray) -> None:
        if not np.isfinite(iterate).all():
            raise NumericalFailure(
                "nonfinite_state", "pressure CG produced a non-finite iterate"
            )

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            solution, info = cg(
                operator,
                compatible_rhs.ravel(),
                M=preconditioner,
                rtol=tolerance,
                atol=0.0,
                maxiter=max_iterations,
                callback=validate_iterate,
            )
    except FloatingPointError as error:
        raise NumericalFailure("projection_failure", f"pressure CG failed: {error}") from error
    if not np.isfinite(solution).all():
        raise NumericalFailure(
            "nonfinite_state", "pressure CG produced a non-finite solution"
        )
    pressure = solution.reshape(ny, nx)
    if np.any(fluid):
        pressure[fluid] -= np.mean(pressure[fluid])
    return pressure, int(info)
