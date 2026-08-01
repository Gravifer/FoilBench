"""Shared array aliases with semantic axis names."""

import numpy as np
from jaxtyping import Bool, Float, Float32, Integer

type ScalarField = Float[np.ndarray, "ny nx"]
type ScalarVolume = Float[np.ndarray, "nz ny nx"]
type VelocityField = Float[np.ndarray, "ny nx dim"]
type CoordinateField = Float[np.ndarray, "ny nx dim"]
type VelocityVolume = Float[np.ndarray, "nz ny nx dim"]
type FaceVelocityX = Float[np.ndarray, "ny nx_face"]
type FaceVelocityY = Float[np.ndarray, "ny_face nx"]
type PointCloud = Float[np.ndarray, "point dim"]
type TracerPositions = Float[np.ndarray, "tracer 2"]
type PathVertices = Float[np.ndarray, "path_vertex 2"]
type ParticleVelocity = Float[np.ndarray, "particle dim"]
type ParticleHistory = Float[np.ndarray, "history particle dim"]
type ParticleScalar = Float[np.ndarray, " particle"]
type ParticleGeneration = Integer[np.ndarray, " particle"]
type ParticleGenerationHistory = Integer[np.ndarray, "history particle"]
type LatticePopulation = Float[np.ndarray, "ny nx direction"]
type MaskField = Bool[np.ndarray, "ny nx"]
type IndexVector = Integer[np.ndarray, " point"]
type ColorBuffer = Float32[np.ndarray, "vertex 4"]
