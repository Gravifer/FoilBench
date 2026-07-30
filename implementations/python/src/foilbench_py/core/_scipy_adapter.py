# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportCallIssue=false
# pyright: reportMissingTypeStubs=false
"""Narrow adapter around SciPy's incompletely typed iterative solver."""

import numpy as np
from scipy.sparse.linalg import LinearOperator, cg


def solve_masked_poisson(
    rhs: np.ndarray,
    fluid: np.ndarray,
    dx: float,
    dy: float,
    tolerance: float = 1.0e-5,
    max_iterations: int = 200,
) -> tuple[np.ndarray, int]:
    """Solve a cell-centered negative Laplacian with a matrix-free CG method."""

    ny, nx = rhs.shape
    inv_dx2 = 1.0 / (dx * dx)
    inv_dy2 = 1.0 / (dy * dy)
    diagonal = 2.0 * inv_dx2 + 2.0 * inv_dy2

    def apply(flat: np.ndarray) -> np.ndarray:
        pressure = flat.reshape(ny, nx)
        padded = np.pad(pressure, 1, mode="constant")
        result = (
            diagonal * pressure
            - inv_dx2 * (padded[1:-1, :-2] + padded[1:-1, 2:])
            - inv_dy2 * (padded[:-2, 1:-1] + padded[2:, 1:-1])
        )
        result = np.where(fluid, result, pressure)
        return result.ravel()

    size = nx * ny
    operator = LinearOperator((size, size), matvec=apply, dtype=rhs.dtype)
    inverse_diagonal = np.where(fluid, 1.0 / diagonal, 1.0).astype(rhs.dtype)
    preconditioner = LinearOperator(
        (size, size),
        matvec=lambda value: inverse_diagonal.ravel() * value,
        dtype=rhs.dtype,
    )
    solution, info = cg(
        operator,
        rhs.ravel(),
        M=preconditioner,
        rtol=tolerance,
        atol=0.0,
        maxiter=max_iterations,
    )
    return solution.reshape(ny, nx), int(info)
