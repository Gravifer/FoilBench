import {describe, expect, it} from "vitest";
import * as THREE from "three";

import {trailBlendConfiguration} from "../../src/viewer/sceneController.js";

describe("SPA trail blending", () => {
  it("maps the three comparison modes to their premultiplied blend equations", () => {
    expect(trailBlendConfiguration("normal")).toEqual({source: THREE.OneFactor, destination: THREE.OneMinusSrcAlphaFactor});
    expect(trailBlendConfiguration("additive")).toEqual({source: THREE.OneFactor, destination: THREE.OneFactor});
    expect(trailBlendConfiguration("screen")).toEqual({source: THREE.OneFactor, destination: THREE.OneMinusSrcColorFactor});
  });
});
