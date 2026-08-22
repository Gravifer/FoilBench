<script lang="ts">
  import {onMount} from "svelte";

  import {isSolverId, SOLVER_IDS} from "../core/contracts.js";
  import type {Scenario, SolverId} from "../core/contracts.js";
  import type {SolverBackend, ViewerSnapshot, ViewerStartState, ViewerStatusEvent} from "../viewer/protocol.js";
  import {FoilSceneController, LAB_PALETTE} from "../viewer/sceneController.js";
  import {fuseViewerStatus} from "../viewer/statusFusion.js";
  import {ViewerWorkerClient} from "../viewer/workerClient.js";
  import {loadPreset, parseScenarioDocument, PRESETS} from "./presets.js";

  const query = new URLSearchParams(location.search);
  const requestedSolver = query.get("solver") ?? "stable-fluids";
  const requestedBackend = query.get("backend") ?? "rust-wasm";
  const requestedPreset = query.get("preset") ?? "dynamic";

  let sceneHost: HTMLDivElement;
  let scene: FoilSceneController | null = null;
  let client: ViewerWorkerClient | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let animationFrame = 0;
  let renderedRevision = -1;
  let dragging = false;

  let scenario = $state.raw<Scenario | null>(null);
  let snapshot = $state.raw<ViewerSnapshot | null>(null);
  let statusEvent = $state.raw<ViewerStatusEvent | null>(null);
  let solverId = $state<SolverId>(isSolverId(requestedSolver) ? requestedSolver : "stable-fluids");
  let backend = $state<SolverBackend>(requestedBackend === "typescript" ? "typescript" : "rust-wasm");
  let presetId = $state(PRESETS.some((preset) => preset.id === requestedPreset) ? requestedPreset : "dynamic");
  let loading = $state(true);
  let error = $state<string | null>(null);
  let controlsOpen = $state(false);
  let teachingOpen = $state(true);
  let diagnosticsOpen = $state(false);
  let tuningSteps = $state<Record<SolverId, number>>({"stable-fluids": 0, "lbm-d2q9": 0, "pic-flip": 0});

  let fused = $derived(snapshot === null ? null : fuseViewerStatus(snapshot, statusEvent));
  let displayedAoa = $derived(snapshot === null ? 0 : (Math.abs(snapshot.angleDegrees) < 0.05 ? 0 : -snapshot.angleDegrees));
  let currentReynolds = $derived(snapshot?.reynolds ?? scenario?.reynolds ?? 1000);
  let phase = $derived(fused?.phase ?? (loading ? "warming" : "failed"));

  const solverLabels: Readonly<Record<SolverId, string>> = {
    "stable-fluids": "Stable Fluids",
    "lbm-d2q9": "D2Q9 LBM",
    "pic-flip": "PIC/FLIP",
  };

  function replaceUrl(): void {
    const parameters = new URLSearchParams();
    if (presetId !== "custom") parameters.set("preset", presetId);
    parameters.set("solver", solverId);
    parameters.set("backend", backend);
    history.replaceState(null, "", `${location.pathname}?${parameters.toString()}`);
  }

  function connect(nextScenario: Scenario, startState?: ViewerStartState): void {
    client?.terminate();
    snapshot = null;
    statusEvent = null;
    renderedRevision = -1;
    loading = true;
    error = null;
    const nextClient = new ViewerWorkerClient();
    client = nextClient;
    nextClient.subscribe((state) => {
      if (state.snapshot !== null) {
        snapshot = state.snapshot;
        loading = false;
      }
      if (state.status !== null) statusEvent = state.status;
      if (state.error !== null) {
        error = state.error;
        loading = false;
      }
    });
    nextClient.initialize(nextScenario, solverId, backend, startState);
    replaceUrl();
  }

  async function choosePreset(nextId: string): Promise<void> {
    const preset = PRESETS.find((candidate) => candidate.id === nextId);
    if (preset === undefined) return;
    presetId = preset.id;
    loading = true;
    error = null;
    try {
      const nextScenario = await loadPreset(preset);
      scenario = nextScenario;
      tuningSteps = {"stable-fluids": 0, "lbm-d2q9": 0, "pic-flip": 0};
      connect(nextScenario);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
      loading = false;
    }
  }

  function changeBackend(nextBackend: SolverBackend): void {
    if (nextBackend === backend || scenario === null) return;
    const startState = snapshot === null ? undefined : {
      angleDegrees: snapshot.angleDegrees,
      reynolds: snapshot.reynolds,
      tuningSteps: tuningSteps[solverId],
    };
    backend = nextBackend;
    connect(scenario, startState);
  }

  function changeSolver(nextSolver: SolverId): void {
    if (nextSolver === solverId) return;
    solverId = nextSolver;
    client?.send({kind: "switch", solverId: nextSolver});
    replaceUrl();
  }

  function setReynolds(value: number): void {
    client?.send({kind: "set-reynolds", reynolds: value});
  }

  function adjustTuning(amount: -1 | 1): void {
    tuningSteps = {...tuningSteps, [solverId]: tuningSteps[solverId] + amount};
    client?.send({kind: "adjust-tuning", amount});
  }

  async function importScenario(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = "";
    if (file === undefined) return;
    loading = true;
    error = null;
    try {
      const document = JSON.parse(await file.text()) as unknown;
      const nextScenario = await parseScenarioDocument(document);
      scenario = nextScenario;
      presetId = "custom";
      tuningSteps = {"stable-fluids": 0, "lbm-d2q9": 0, "pic-flip": 0};
      connect(nextScenario);
    } catch (reason) {
      error = `Scenario rejected: ${reason instanceof Error ? reason.message : String(reason)}`;
      loading = false;
    }
  }

  function metric(name: string, digits = 3): string {
    return snapshot?.diagnostics[name]?.toFixed(digits) ?? "—";
  }

  function handleKey(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (target?.matches("input, select, textarea, button") === true) return;
    if (event.key === " ") { event.preventDefault(); client?.send({kind: "pause"}); }
    else if (event.key.toLowerCase() === "r") client?.send({kind: "reset"});
    else if (event.key === "1" || event.key === "2" || event.key === "3") changeSolver(SOLVER_IDS[Number(event.key) - 1] ?? "stable-fluids");
    else if (event.key === "+" || event.key === "=") setReynolds(currentReynolds * 10 ** 0.25);
    else if (event.key === "-") setReynolds(currentReynolds / 10 ** 0.25);
    else if (event.key === "0" && scenario !== null) setReynolds(scenario.reynolds);
    else if (event.key === "[") adjustTuning(-1);
    else if (event.key === "]") adjustTuning(1);
    else if (event.key.toLowerCase() === "v") client?.send({kind: "toggle-vorticity"});
    else if (event.key.toLowerCase() === "d") client?.send({kind: "toggle-diagnostics"});
    else if (event.key.toLowerCase() === "t") client?.send({kind: "toggle-tracers"});
    else if (event.key.toLowerCase() === "c") client?.send({kind: "toggle-crop"});
  }

  onMount(() => {
    scene = new FoilSceneController(sceneHost, LAB_PALETTE);
    const resize = (): void => {
      const bounds = sceneHost.getBoundingClientRect();
      scene?.resize(bounds.width, bounds.height);
      renderedRevision = -1;
    };
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(sceneHost);
    resize();

    const canvas = scene.canvas;
    const releasePointer = (event: PointerEvent): void => {
      if (!dragging) return;
      dragging = false;
      client?.releaseAngle();
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    };
    const pointerDown = (event: PointerEvent): void => {
      if (event.button !== 0) return;
      dragging = true;
      canvas.setPointerCapture(event.pointerId);
    };
    const pointerMove = (event: PointerEvent): void => {
      if (dragging && scenario !== null && scene !== null) client?.queueAngle(scene.pointerAngle(event, scenario));
    };
    canvas.addEventListener("pointerdown", pointerDown);
    canvas.addEventListener("pointermove", pointerMove);
    canvas.addEventListener("pointerup", releasePointer);
    canvas.addEventListener("pointercancel", releasePointer);
    canvas.addEventListener("lostpointercapture", releasePointer);

    const draw = (): void => {
      animationFrame = requestAnimationFrame(draw);
      if (snapshot === null || scenario === null || scene === null) return;
      if (snapshot.revision !== renderedRevision) {
        renderedRevision = snapshot.revision;
        scene.render(snapshot, scenario);
        client?.acknowledgeSnapshot(snapshot.revision);
      } else scene.draw();
    };
    draw();
    window.addEventListener("keydown", handleKey);
    const visibility = (): void => { client?.setVisible(document.visibilityState === "visible"); };
    document.addEventListener("visibilitychange", visibility);
    void choosePreset(presetId);

    return () => {
      cancelAnimationFrame(animationFrame);
      resizeObserver?.disconnect();
      window.removeEventListener("keydown", handleKey);
      document.removeEventListener("visibilitychange", visibility);
      client?.shutdown();
      client?.terminate();
      scene?.dispose();
    };
  });
</script>

<svelte:head>
  <meta property="og:title" content="FoilBench" />
  <meta property="og:description" content="An interactive browser lab for airflow, separation, and wakes." />
</svelte:head>

<main class="lab-shell">
  <header class="lab-header">
    <div>
      <h1>FoilBench</h1>
      <p>Make the invisible flow visible.</p>
    </div>
    <div class="header-status" aria-live="polite">
      <span class:status-running={phase === "running"} class:status-warning={phase === "warming" || phase === "paused"} class:status-failed={phase === "failed"}></span>
      <span>{phase}</span>
      <span class="header-backend">{backend === "rust-wasm" ? "Rust / WASM" : "TypeScript"}</span>
    </div>
    <button class="mobile-control-button" type="button" aria-expanded={controlsOpen} onclick={() => controlsOpen = !controlsOpen}>Controls</button>
  </header>

  <section class="flow-stage" aria-label="Interactive airflow visualization">
    <div class="scene-host" bind:this={sceneHost}></div>
    {#if loading}
      <div class="stage-message"><strong>Preparing the flow</strong><span>The numerical backend is warming up.</span></div>
    {:else if error !== null}
      <div class="stage-message stage-error"><strong>Simulation unavailable</strong><span>{error}</span></div>
    {/if}
    <div class="stage-readout">
      <div><span>Angle of attack</span><strong>{displayedAoa.toFixed(1)}°</strong></div>
      <div><span>Reynolds number</span><strong>{currentReynolds.toFixed(0)}</strong></div>
      <div><span>Solver rate</span><strong>{snapshot?.stepRate?.toFixed(1) ?? "—"}<small> steps/s</small></strong></div>
      <div><span>Flow time</span><strong>{snapshot?.time.toFixed(2) ?? "—"}<small> s</small></strong></div>
    </div>
    <p class="drag-hint">Drag around the foil to change its angle</p>
  </section>

  <aside class:controls-open={controlsOpen} class="control-panel" aria-label="Simulation controls">
    <div class="panel-scroll">
      <section class="control-section">
        <div class="section-heading"><h2>Experiment</h2><span>{scenario?.foil.naca === undefined ? "" : `NACA ${scenario.foil.naca}`}</span></div>
        <label class="field-label" for="preset">Preset</label>
        <select id="preset" value={presetId} onchange={(event) => void choosePreset(event.currentTarget.value)}>
          {#each PRESETS as preset}
            <option value={preset.id}>{preset.label}{preset.expensive === true ? " · heavy" : ""}</option>
          {/each}
          {#if presetId === "custom"}<option value="custom">Imported scenario</option>{/if}
        </select>
        <p class="field-note">{PRESETS.find((preset) => preset.id === presetId)?.summary ?? "A locally imported, schema-validated scenario."}</p>
        <label class="file-button">Import scenario<input type="file" accept="application/json,.json" onchange={(event) => void importScenario(event)} /></label>
      </section>

      <section class="control-section">
        <div class="section-heading"><h2>Numerical model</h2><span>runs locally</span></div>
        <div class="segmented" aria-label="Numerical backend">
          <button class:active={backend === "rust-wasm"} type="button" onclick={() => changeBackend("rust-wasm")}>Rust / WASM</button>
          <button class:active={backend === "typescript"} type="button" onclick={() => changeBackend("typescript")}>TypeScript</button>
        </div>
        <div class="solver-grid">
          {#each SOLVER_IDS as id, index}
            <button class:active={solverId === id} type="button" onclick={() => changeSolver(id)}><span>{index + 1}</span>{solverLabels[id]}</button>
          {/each}
        </div>
      </section>

      <section class="control-section">
        <div class="transport-row">
          <button class="primary-action" type="button" onclick={() => client?.send({kind: "pause"})}>{snapshot?.paused === true ? "Resume" : "Pause"}</button>
          <button type="button" onclick={() => client?.send({kind: "reset"})}>Reset</button>
        </div>
        <label class="range-label" for="angle"><span>Angle of attack</span><output>{displayedAoa.toFixed(1)}°</output></label>
        <input id="angle" type="range" min="-30" max="30" step="0.5" value={displayedAoa} oninput={(event) => client?.queueAngle(-Number(event.currentTarget.value))} onchange={() => client?.releaseAngle()} />
        <label class="range-label" for="reynolds"><span>Reynolds number</span><output>{currentReynolds.toFixed(0)}</output></label>
        <input id="reynolds" type="range" min={Math.log10(50)} max={5} step="0.05" value={Math.log10(currentReynolds)} oninput={(event) => setReynolds(10 ** Number(event.currentTarget.value))} />
        <div class="tuning-row"><span>{snapshot?.solverTuning ?? "tuning=—"}</span><button type="button" aria-label="Decrease solver tuning" onclick={() => adjustTuning(-1)}>[</button><button type="button" aria-label="Increase solver tuning" onclick={() => adjustTuning(1)}>]</button></div>
      </section>

      <section class="control-section">
        <div class="section-heading"><h2>View</h2><span>presentation only</span></div>
        <div class="toggle-grid">
          <button class:active={snapshot?.vorticityVisible === true} type="button" onclick={() => client?.send({kind: "toggle-vorticity"})}>Vorticity</button>
          <button class:active={snapshot?.tracerMode === "material"} type="button" onclick={() => client?.send({kind: "toggle-tracers"})}>Material tracers</button>
          <button class:active={snapshot?.cropEnabled === true} type="button" onclick={() => client?.send({kind: "toggle-crop"})}>Crop edges</button>
          <button class:active={snapshot?.diagnosticMode === "every-step"} type="button" onclick={() => client?.send({kind: "toggle-diagnostics"})}>Live diagnostics</button>
        </div>
      </section>

      <section class="explanation-card">
        <button type="button" aria-expanded={teachingOpen} onclick={() => teachingOpen = !teachingOpen}><span>What to watch</span><span>{teachingOpen ? "−" : "+"}</span></button>
        {#if teachingOpen}
          <div class="explanation-copy">
            <p><strong>Attached flow</strong> follows the foil at modest angles.</p>
            <p><strong>Separation</strong> begins when the upper flow can no longer follow the surface.</p>
            <p><strong>The wake remembers.</strong> Return the foil gently and the disturbed flow takes time to settle.</p>
          </div>
        {/if}
      </section>

      <section class="diagnostics-card">
        <button type="button" aria-expanded={diagnosticsOpen} onclick={() => diagnosticsOpen = !diagnosticsOpen}><span>Diagnostics</span><span>{diagnosticsOpen ? "−" : "+"}</span></button>
        {#if diagnosticsOpen}
          <dl>
            <div><dt>Kinetic energy</dt><dd>{metric("kinetic_energy")}</dd></div>
            <div><dt>Enstrophy Ω</dt><dd>{metric("enstrophy")}</dd></div>
            <div><dt>Divergence</dt><dd>{metric("divergence_linf")}</dd></div>
            <div><dt>Wall leakage</dt><dd>{metric("solid_leakage")}</dd></div>
            <div><dt>Max |u|</dt><dd>{snapshot?.maxSpeed.toFixed(2) ?? "—"}</dd></div>
            <div><dt>Sim / wall</dt><dd>{snapshot?.simulatedPerWall?.toFixed(2) ?? "—"}</dd></div>
          </dl>
        {/if}
      </section>
    </div>
  </aside>

  <footer class="lab-footer">
    <span class="status-copy" aria-live="polite">{error ?? fused?.pendingStatus ?? fused?.status ?? "Loading the simulation…"}</span>
    <span class="shortcut-copy">Space pause · R reset · 1/2/3 solver · V vorticity · C crop</span>
  </footer>
</main>
