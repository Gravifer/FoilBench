import * as THREE from "three";

import type {Scenario} from "../core/contracts.js";
import {isSpaViewerSnapshot} from "./protocol.js";
import type {ViewerSnapshot} from "./protocol.js";
import {vorticityRgba} from "./vorticityTexture.js";

export interface FoilScenePalette {
  readonly background: number;
  readonly flowPath: number;
  readonly flowPoint: number;
  readonly foil: number;
}

export interface FoilSceneStyle {
  readonly showTracerPoints?: boolean;
  readonly trailStyle?: "reference" | "age-speed-alpha";
}

const REFERENCE_PALETTE: FoilScenePalette = {
  background: 0x000000,
  flowPath: 0x148cff,
  flowPoint: 0x3ba5ff,
  foil: 0xe7eef8,
};

export const LAB_PALETTE: FoilScenePalette = {
  background: 0x1c1c1c,
  flowPath: 0x58c4dd,
  flowPoint: 0x9cdceb,
  foil: 0xffffff,
};

export class FoilSceneController {
  public readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.OrthographicCamera(-4, 4, 2, -2, -10, 10);
  private readonly paths: THREE.LineSegments;
  private readonly points: THREE.Points;
  private readonly foil: THREE.LineLoop;
  private readonly vorticityCanvas = document.createElement("canvas");
  private readonly vorticityTexture = new THREE.CanvasTexture(this.vorticityCanvas);
  private readonly vorticityMaterial: THREE.MeshBasicMaterial;
  private readonly vorticityPlane: THREE.Mesh;
  private readonly trailStyle: "reference" | "age-speed-alpha";
  private width = 1;
  private height = 1;

  public constructor(
    private readonly host: HTMLElement,
    private readonly palette: FoilScenePalette = REFERENCE_PALETTE,
    style: FoilSceneStyle = {},
  ) {
    this.renderer = new THREE.WebGLRenderer({antialias: true});
    this.renderer.setPixelRatio(devicePixelRatio);
    this.renderer.setClearColor(this.palette.background);
    this.canvas = this.renderer.domElement;
    this.canvas.classList.add("foilbench-canvas");
    this.host.append(this.canvas);

    this.camera.position.z = 5;
    this.trailStyle = style.trailStyle ?? "reference";
    this.paths = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      this.trailStyle === "age-speed-alpha"
        ? new THREE.ShaderMaterial({
            uniforms: {flowColor: {value: new THREE.Color(this.palette.flowPath)}},
            vertexShader: "attribute float intensity; varying float vIntensity; void main() { vIntensity = intensity; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
            fragmentShader: "uniform vec3 flowColor; varying float vIntensity; void main() { gl_FragColor = vec4(flowColor, vIntensity); }",
            transparent: true,
            depthWrite: false,
            blending: THREE.NormalBlending,
          })
        : new THREE.LineBasicMaterial({color: this.palette.flowPath, transparent: true, opacity: 0.55}),
    );
    this.points = new THREE.Points(
      new THREE.BufferGeometry(),
      new THREE.PointsMaterial({color: this.palette.flowPoint, size: 2, sizeAttenuation: false}),
    );
    this.points.visible = style.showTracerPoints ?? true;
    this.foil = new THREE.LineLoop(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({color: this.palette.foil}),
    );
    this.scene.add(this.paths, this.points, this.foil);

    this.vorticityTexture.minFilter = THREE.LinearFilter;
    this.vorticityTexture.magFilter = THREE.LinearFilter;
    this.vorticityMaterial = new THREE.MeshBasicMaterial({
      map: this.vorticityTexture,
      transparent: true,
      depthWrite: false,
    });
    this.vorticityPlane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), this.vorticityMaterial);
    this.vorticityPlane.position.z = -1;
    this.scene.add(this.vorticityPlane);
  }

  public resize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.renderer.setSize(this.width, this.height, false);
  }

  public reframe(snapshot: ViewerSnapshot, scenario: Scenario): void {
    this.updateCamera(snapshot, scenario);
    this.renderer.render(this.scene, this.camera);
  }

  public render(snapshot: ViewerSnapshot, scenario: Scenario): void {
    this.updateCamera(snapshot, scenario);
    this.updateGeometry(this.paths.geometry, snapshot.pathSegments);
    if (this.trailStyle === "age-speed-alpha" && isSpaViewerSnapshot(snapshot)) this.updateTrailIntensity(snapshot, scenario);
    this.updateGeometry(this.points.geometry, snapshot.tracerPositions);
    this.updateGeometry(this.foil.geometry, snapshot.foilOutline);
    this.updateVorticity(snapshot);
    this.renderer.render(this.scene, this.camera);
  }

  public draw(): void {
    this.renderer.render(this.scene, this.camera);
  }

  public pointerAngle(event: PointerEvent, scenario: Scenario): number {
    const rect = this.canvas.getBoundingClientRect();
    const worldX = this.camera.left + (event.clientX - rect.left) / rect.width * (this.camera.right - this.camera.left);
    const worldY = this.camera.top - (event.clientY - rect.top) / rect.height * (this.camera.top - this.camera.bottom);
    const angle = Math.atan2(
      worldY - (scenario.foil.pivot[1] ?? 0),
      worldX - (scenario.foil.pivot[0] ?? 0),
    ) * 180 / Math.PI;
    return Math.max(-30, Math.min(30, angle));
  }

  public dispose(): void {
    this.paths.geometry.dispose();
    this.points.geometry.dispose();
    this.foil.geometry.dispose();
    (this.paths.material as THREE.Material).dispose();
    (this.points.material as THREE.Material).dispose();
    (this.foil.material as THREE.Material).dispose();
    this.vorticityPlane.geometry.dispose();
    this.vorticityMaterial.dispose();
    this.vorticityTexture.dispose();
    this.renderer.dispose();
    this.canvas.remove();
  }

  private updateCamera(snapshot: ViewerSnapshot, scenario: Scenario): void {
    const [[x0, x1], [y0, y1]] = snapshot.bounds;
    const crop = snapshot.cropEnabled ? (scenario.solverOptions.viewerCropCells ?? 0) : 0;
    const dx = (x1 - x0) / snapshot.resolution[0];
    const dy = (y1 - y0) / snapshot.resolution[1];
    let left = x0 + crop * dx;
    let right = x1 - crop * dx;
    let bottom = y0 + crop * dy;
    let top = y1 - crop * dy;
    const domainAspect = (right - left) / (top - bottom);
    const viewportAspect = this.width / this.height;
    if (viewportAspect > domainAspect) {
      const extra = 0.5 * ((top - bottom) * viewportAspect - (right - left));
      left -= extra;
      right += extra;
    } else {
      const extra = 0.5 * ((right - left) / viewportAspect - (top - bottom));
      bottom -= extra;
      top += extra;
    }
    this.camera.left = left;
    this.camera.right = right;
    this.camera.bottom = bottom;
    this.camera.top = top;
    this.camera.updateProjectionMatrix();
  }

  private updateVorticity(snapshot: ViewerSnapshot): void {
    this.vorticityPlane.visible = snapshot.vorticityVisible;
    if (!snapshot.vorticityVisible || snapshot.vorticity.length === 0) return;
    const [nx, ny] = snapshot.resolution;
    this.vorticityCanvas.width = nx;
    this.vorticityCanvas.height = ny;
    const context = this.vorticityCanvas.getContext("2d");
    if (context === null) return;
    const image = context.createImageData(nx, ny);
    image.data.set(vorticityRgba(snapshot.vorticity, nx, ny));
    context.putImageData(image, 0, 0);
    this.vorticityTexture.needsUpdate = true;
    const [[x0, x1], [y0, y1]] = snapshot.bounds;
    this.vorticityPlane.scale.set(x1 - x0, y1 - y0, 1);
    this.vorticityPlane.position.set((x0 + x1) / 2, (y0 + y1) / 2, -1);
  }

  private updateGeometry(geometry: THREE.BufferGeometry, values: Float32Array): void {
    const vertexCount = values.length / 2;
    let attribute = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (attribute === undefined || attribute.array.length < 3 * vertexCount) {
      let capacity = 3;
      while (capacity < 3 * vertexCount) capacity *= 2;
      attribute = new THREE.BufferAttribute(new Float32Array(capacity), 3);
      attribute.setUsage(THREE.DynamicDrawUsage);
      geometry.setAttribute("position", attribute);
    }
    const xyz = attribute.array as Float32Array;
    for (let index = 0; index < vertexCount; index += 1) {
      xyz[3 * index] = values[2 * index] ?? 0;
      xyz[3 * index + 1] = values[2 * index + 1] ?? 0;
      xyz[3 * index + 2] = 0;
    }
    attribute.needsUpdate = true;
    geometry.setDrawRange(0, vertexCount);
  }

  private updateTrailIntensity(snapshot: ViewerSnapshot & {readonly pathAges: Uint8Array}, scenario: Scenario): void {
    const segmentCount = snapshot.pathSegments.length / 4;
    if (snapshot.pathAges.length !== segmentCount) throw new RangeError("path age metadata does not align with path segments");
    const vertexCount = 2 * segmentCount;
    let attribute = this.paths.geometry.getAttribute("intensity") as THREE.BufferAttribute | undefined;
    if (attribute === undefined || attribute.array.length < vertexCount) {
      let capacity = 1;
      while (capacity < vertexCount) capacity *= 2;
      attribute = new THREE.BufferAttribute(new Float32Array(capacity), 1);
      attribute.setUsage(THREE.DynamicDrawUsage);
      this.paths.geometry.setAttribute("intensity", attribute);
    }
    const intensities = attribute.array as Float32Array;
    const referenceSpeed = Math.max(Math.hypot(scenario.freestream[0] ?? 0, scenario.freestream[1] ?? 0), 1e-6);
    const dt = Math.max(scenario.outputDt, 1e-9);
    for (let segment = 0; segment < segmentCount; segment += 1) {
      const scalar = 4 * segment;
      const speed = Math.hypot(
        (snapshot.pathSegments[scalar + 2] ?? 0) - (snapshot.pathSegments[scalar] ?? 0),
        (snapshot.pathSegments[scalar + 3] ?? 0) - (snapshot.pathSegments[scalar + 1] ?? 0),
      ) / dt;
      const age = (snapshot.pathAges[segment] ?? 0) / 255;
      const speedFactor = Math.sqrt(Math.min(1, speed / (1.75 * referenceSpeed)));
      const intensity = (0.08 + 0.92 * age ** 1.3) * (0.35 + 0.65 * speedFactor);
      intensities[2 * segment] = intensity;
      intensities[2 * segment + 1] = intensity;
    }
    attribute.needsUpdate = true;
  }
}
