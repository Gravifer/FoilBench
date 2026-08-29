<script lang="ts">
  import {onMount} from "svelte";

  import {isSolverId, SOLVER_IDS} from "foilbench-typescript/src/core/contracts.js";
  import type {InteractiveTuning, Scenario, SolverId} from "foilbench-typescript/src/core/contracts.js";
  import {isSpaViewerSnapshot} from "foilbench-typescript/src/viewer/protocol.js";
  import type {SolverBackend, SpaViewerSnapshot, ViewerStartState, ViewerStatusEvent} from "foilbench-typescript/src/viewer/protocol.js";
  import {FoilSceneController, LAB_PALETTE} from "foilbench-typescript/src/viewer/sceneController.js";
  import type {TrailBlendMode} from "foilbench-typescript/src/viewer/sceneController.js";
  import {fuseViewerStatus} from "foilbench-typescript/src/viewer/statusFusion.js";
  import {ViewerWorkerClient} from "foilbench-typescript/src/viewer/workerClient.js";
  import {LatestRequestGate} from "./latestRequest.js";
  import {loadPreset, parseScenarioDocument, PRESETS} from "./presets.js";
  import {StatusNoticeController} from "./statusNotice.js";

  const query = new URLSearchParams(location.search);
  const requestedSolver = query.get("solver") ?? "stable-fluids";
  const requestedBackend = query.get("backend") ?? "typescript";
  const requestedPreset = query.get("preset") ?? "dynamic";
  const narrowControlsQuery = window.matchMedia("(max-width: 980px)");
  const STATUS_NOTICE_MILLISECONDS = 8000;

  interface SceneLayout {
    readonly left: number;
    readonly top: number;
    readonly width: number;
    readonly height: number;
    readonly headerOverlap: number;
  }

  let labHeader: HTMLElement;
  let flowViewport: HTMLDivElement;
  let sceneHost: HTMLDivElement;
  let scene: FoilSceneController | null = null;
  let client: ViewerWorkerClient | null = null;
  let resizeObserver: ResizeObserver | null = null;
  let animationFrame = 0;
  let renderedRevision = -1;
  let dragging = false;
  const scenarioRequests = new LatestRequestGate();

  let scenario = $state.raw<Scenario | null>(null);
  let snapshot = $state.raw<SpaViewerSnapshot | null>(null);
  let statusEvent = $state.raw<ViewerStatusEvent | null>(null);
  let solverId = $state<SolverId>(isSolverId(requestedSolver) ? requestedSolver : "stable-fluids");
  let backend = $state<SolverBackend>(requestedBackend === "rust-wasm" ? "rust-wasm" : "typescript");
  let presetId = $state(PRESETS.some((preset) => preset.id === requestedPreset) ? requestedPreset : "dynamic");
  let loading = $state(true);
  let error = $state<string | null>(null);
  let narrowViewport = $state(narrowControlsQuery.matches);
  let sceneLayout = $state.raw<SceneLayout>({left: 0, top: 0, width: window.innerWidth, height: window.innerHeight, headerOverlap: 0});
  let wideControlsOpen = $state(true);
  let narrowControlsOpen = $state(false);
  let teachingOpen = $state(true);
  let diagnosticsOpen = $state(false);
  let trailBlendMode = $state<TrailBlendMode>("normal");
  let statusNotice = $state<string | null>(null);
  const statusNotices = new StatusNoticeController((notice) => { statusNotice = notice; });
  let tuningSteps = $state<Record<SolverId, number>>({"stable-fluids": 0, "lbm-d2q9": 0, "pic-flip": 0});

  let fused = $derived(snapshot === null ? null : fuseViewerStatus(snapshot, statusEvent));
  let displayedAoa = $derived(snapshot === null ? 0 : (Math.abs(snapshot.angleDegrees) < 0.05 ? 0 : -snapshot.angleDegrees));
  let currentReynolds = $derived(snapshot?.reynolds ?? scenario?.reynolds ?? 1000);
  let phase = $derived(fused?.phase ?? (loading ? "warming" : "failed"));
  let tuning = $derived(snapshot?.solverTuning ?? null);
  let controlsOpen = $derived(narrowViewport ? narrowControlsOpen : wideControlsOpen);
  let detailStatus = $derived.by(() => {
    if (fused?.pendingStatus !== null && fused?.pendingStatus !== undefined) return fused.pendingStatus;
    const status = fused?.status;
    return status === undefined || status === "running" || status === "warming" || status === "motion resolved; running" ? null : status;
  });
  let detailStatusIdentity = $derived(detailStatus === null ? null : [
    statusEvent?.appliedCommand ?? snapshot?.appliedCommand ?? -1,
    statusEvent?.revision ?? snapshot?.revision ?? -1,
    statusEvent?.solverEpoch ?? snapshot?.solverEpoch ?? -1,
    statusEvent?.recoveryEpoch ?? snapshot?.recoveryEpoch ?? -1,
    detailStatus,
  ].join(":"));

  function updateSceneLayout(nextScenario: Scenario | null = scenario): void {
    const xBounds = nextScenario?.domain.bounds[0];
    const yBounds = nextScenario?.domain.bounds[1];
    if (xBounds === undefined || yBounds === undefined || flowViewport === undefined || labHeader === undefined) return;
    const domainAspect = (xBounds[1] - xBounds[0]) / (yBounds[1] - yBounds[0]);
    const availableWidth = flowViewport.clientWidth;
    const availableHeight = flowViewport.clientHeight;
    const headerHeight = labHeader.getBoundingClientRect().height;
    const belowHeaderHeight = Math.max(0, availableHeight - headerHeight);
    const naturalHeight = availableWidth / domainAspect;
    if (naturalHeight <= belowHeaderHeight) {
      sceneLayout = {
        left: 0,
        top: headerHeight + 0.5 * (belowHeaderHeight - naturalHeight),
        width: availableWidth,
        height: naturalHeight,
        headerOverlap: 0,
      };
    } else if (naturalHeight < availableHeight) {
      const top = availableHeight - naturalHeight;
      sceneLayout = {
        left: 0,
        top,
        width: availableWidth,
        height: naturalHeight,
        headerOverlap: Math.max(0, headerHeight - top),
      };
    } else {
      const width = availableHeight * domainAspect;
      sceneLayout = {
        left: 0.5 * (availableWidth - width),
        top: 0,
        width,
        height: availableHeight,
        headerOverlap: headerHeight,
      };
    }
  }

  $effect(() => {
    if (detailStatusIdentity === null || detailStatus === null) return;
    statusNotices.show(detailStatusIdentity, detailStatus, STATUS_NOTICE_MILLISECONDS);
  });

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
      if (state.snapshot !== null && isSpaViewerSnapshot(state.snapshot)) {
        snapshot = state.snapshot;
        loading = false;
      }
      if (state.status !== null) statusEvent = state.status;
      if (state.error !== null) {
        error = state.error;
        loading = false;
      }
    });
    nextClient.initialize(nextScenario, solverId, backend, startState, "spa");
    replaceUrl();
  }

  async function choosePreset(nextId: string): Promise<void> {
    const preset = PRESETS.find((candidate) => candidate.id === nextId);
    if (preset === undefined) return;
    presetId = preset.id;
    loading = true;
    error = null;
    try {
      const result = await scenarioRequests.run(() => loadPreset(preset));
      if (!result.current) return;
      const nextScenario = result.value;
      scenario = nextScenario;
      updateSceneLayout(nextScenario);
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
    if (tuning === null || (amount < 0 && !tuning.canDecrease) || (amount > 0 && !tuning.canIncrease)) return;
    tuningSteps = {...tuningSteps, [solverId]: tuningSteps[solverId] + amount};
    client?.send({kind: "adjust-tuning", amount});
  }

  function tuningTitle(selected: InteractiveTuning): string {
    if (selected.id === "stable-advection") {
      const names: Readonly<Record<string, string>> = {"semi-lagrangian": "Semi-Lagrangian", maccormack: "MacCormack", "skew-rk2": "Skew RK2"};
      return names[String(selected.value)] ?? String(selected.value);
    }
    if (selected.id === "pic-flip-blend" && typeof selected.value === "number") return `${Math.round(100 * selected.value)}% FLIP · ${Math.round(100 * (1 - selected.value))}% PIC`;
    return String(selected.value);
  }

  function tuningDescription(selected: InteractiveTuning): string {
    if (selected.id === "stable-advection") {
      const descriptions: Readonly<Record<string, string>> = {
        "semi-lagrangian": "Smooth and forgiving transport",
        maccormack: "Sharper balanced transport",
        "skew-rk2": "Energetic eddy-preserving experiment",
      };
      return descriptions[String(selected.value)] ?? selected.label;
    }
    return selected.id === "pic-flip-blend" ? "Particle velocity update" : selected.label;
  }

  async function importScenario(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = "";
    if (file === undefined) return;
    loading = true;
    error = null;
    try {
      const result = await scenarioRequests.run(async () => {
        const document = JSON.parse(await file.text()) as unknown;
        return parseScenarioDocument(document);
      });
      if (!result.current) return;
      const nextScenario = result.value;
      scenario = nextScenario;
      updateSceneLayout(nextScenario);
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

  function toggleControls(): void {
    if (narrowViewport) narrowControlsOpen = !narrowControlsOpen;
    else wideControlsOpen = !wideControlsOpen;
  }

  function changeTrailBlendMode(mode: TrailBlendMode): void {
    trailBlendMode = mode;
    scene?.setTrailBlendMode(mode);
  }

  function handleKey(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape" && narrowViewport && narrowControlsOpen) { narrowControlsOpen = false; return; }
    if (target !== null && target.closest("input, select, textarea, button, [contenteditable='true']") !== null) return;
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
    scene = new FoilSceneController(sceneHost, LAB_PALETTE, {showTracerPoints: false, trailStyle: "age-speed-alpha", trailBlendMode});
    const resize = (): void => {
      updateSceneLayout();
      const bounds = sceneHost.getBoundingClientRect();
      scene?.resize(bounds.width, bounds.height);
      if (snapshot !== null && scenario !== null) scene?.reframe(snapshot, scenario);
      else renderedRevision = -1;
    };
    resize();
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(sceneHost);
    resizeObserver.observe(flowViewport);
    resizeObserver.observe(labHeader);

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
    const updateControlsMode = (event: MediaQueryListEvent): void => { narrowViewport = event.matches; };
    narrowControlsQuery.addEventListener("change", updateControlsMode);
    const visibility = (): void => { client?.setVisible(document.visibilityState === "visible"); };
    document.addEventListener("visibilitychange", visibility);
    void choosePreset(presetId);

    return () => {
      scenarioRequests.invalidate();
      cancelAnimationFrame(animationFrame);
      statusNotices.dispose();
      resizeObserver?.disconnect();
      window.removeEventListener("keydown", handleKey);
      narrowControlsQuery.removeEventListener("change", updateControlsMode);
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

<main class:panel-open={controlsOpen} class="lab-shell">
  <header class="lab-header" bind:this={labHeader}>
    <div class="header-left">
      <div class="header-identity">
        <h1>FoilBench</h1>
        <p>Toy 2D wind tunnel with an airfoil</p>
      </div>
      <label class="header-preset" for="preset"><span>Experiment</span>
        <select id="preset" value={presetId} onchange={(event) => void choosePreset(event.currentTarget.value)}>
          {#each PRESETS as preset}
            <option value={preset.id}>{preset.label}{preset.expensive === true ? " · heavy" : ""}</option>
          {/each}
          {#if presetId === "custom"}<option value="custom">Imported scenario</option>{/if}
        </select>
      </label>
    </div>
    <div class="header-playback">
      <div class="header-transport" aria-label="Simulation transport">
        <button class="transport-icon" type="button" aria-label="Reset simulation" aria-keyshortcuts="R" title="Reset (R)" onclick={() => client?.send({kind: "reset"})}><span aria-hidden="true">↺</span></button>
        <button class="transport-icon primary-action" type="button" aria-label={snapshot?.paused === true ? "Resume simulation" : "Pause simulation"} aria-keyshortcuts="Space" title={snapshot?.paused === true ? "Resume (Space)" : "Pause (Space)"} onclick={() => client?.send({kind: "pause"})}><span aria-hidden="true">{snapshot?.paused === true ? "▶︎" : "⏸︎"}</span></button>
      </div>
      <div class="playback-meta">
        <span class="playback-time">t={snapshot?.time.toFixed(2) ?? "—"}</span>
        <div class="header-status" class:status-running={phase === "running"} class:status-warming={phase === "warming"} class:status-paused={phase === "paused"} class:status-failed={phase === "failed"} aria-label={`Simulation ${phase}`} aria-live="polite">
          <span class="status-bulb" aria-hidden="true"></span>
          {#if phase !== "running"}<span class="status-label">{phase}</span>{/if}
        </div>
      </div>
    </div>
    <div class="header-right">
      <button class="controls-toggle" type="button" aria-controls="simulation-controls" aria-expanded={controlsOpen} aria-label={controlsOpen ? "Hide controls" : "Show controls"} title={controlsOpen ? "Hide controls" : "Show controls"} onclick={toggleControls}>
        <svg aria-hidden="true" viewBox="0 0 20 20"><path d="M3 5h14M3 10h14M3 15h14" /></svg>
      </button>
    </div>
  </header>

  <div class="flow-viewport" bind:this={flowViewport}>
    <section
      class="flow-stage"
      aria-label="Interactive airflow visualization"
      style={`left:${sceneLayout.left}px; top:${sceneLayout.top}px; width:${sceneLayout.width}px; height:${sceneLayout.height}px; --scene-header-overlap:${sceneLayout.headerOverlap}px;`}
    >
      <div class="scene-host" bind:this={sceneHost}></div>
      {#if loading}
        <div class="stage-message"><strong>Preparing the flow</strong><span>The numerical backend is warming up.</span></div>
      {:else if error !== null}
        <div class="stage-message stage-error"><strong>Simulation unavailable</strong><span>{error}</span></div>
      {/if}
      {#if statusNotice !== null}<p class="stage-status" aria-live="polite">{statusNotice}</p>{/if}
      <div class="stage-readout">
        <div><span>Angle of attack</span><strong>{displayedAoa.toFixed(1)}°</strong></div>
        <div><span>Reynolds number</span><strong>{currentReynolds.toFixed(0)}</strong></div>
        <div><span>Solver rate</span><strong>{snapshot?.stepRate?.toFixed(1) ?? "—"}<small> steps/s</small></strong></div>
      </div>
      <p class="drag-hint">Drag around the foil to change its angle</p>
    </section>
  </div>

  <aside id="simulation-controls" class:controls-open={controlsOpen} class="control-panel" aria-label="Simulation controls">
    <div class="panel-scroll">
      <section class="control-section">
        <div class="section-heading"><h2>Experiment</h2><span>{scenario?.foil.naca === undefined ? "" : `NACA ${scenario.foil.naca}`}</span></div>
        <label class="mobile-preset-field" for="mobile-preset"><span class="field-label">Preset</span>
          <select id="mobile-preset" value={presetId} onchange={(event) => void choosePreset(event.currentTarget.value)}>
            {#each PRESETS as preset}
              <option value={preset.id}>{preset.label}{preset.expensive === true ? " · heavy" : ""}</option>
            {/each}
            {#if presetId === "custom"}<option value="custom">Imported scenario</option>{/if}
          </select>
        </label>
        <p class="field-note">{PRESETS.find((preset) => preset.id === presetId)?.summary ?? "A locally imported, schema-validated scenario."}</p>
        <label class="file-button">Import scenario<input type="file" accept="application/json,.json" onchange={(event) => void importScenario(event)} /></label>
      </section>

      <section class="control-section">
        <div class="section-heading"><h2>Solver</h2><span>runs locally</span></div>
        <span class="field-label">Method</span>
        <div class="solver-grid">
          {#each SOLVER_IDS as id, index}
            <button class:active={solverId === id} type="button" aria-keyshortcuts={String(index + 1)} onclick={() => changeSolver(id)}><span>{solverLabels[id]}</span><kbd>{index + 1}</kbd></button>
          {/each}
        </div>
        <span class="field-label engine-label">Execution engine</span>
        <div class="segmented" aria-label="Execution engine">
          <button class:active={backend === "typescript"} type="button" onclick={() => changeBackend("typescript")}>TypeScript</button>
          <button class:active={backend === "rust-wasm"} type="button" onclick={() => changeBackend("rust-wasm")}>Rust / WASM</button>
        </div>
      </section>

      <section class="control-section">
        <label class="range-label" for="angle"><span>Angle of attack</span><output>{displayedAoa.toFixed(1)}°</output></label>
        <input id="angle" type="range" min="-30" max="30" step="0.5" value={displayedAoa} oninput={(event) => client?.queueAngle(-Number(event.currentTarget.value))} onchange={() => client?.releaseAngle()} />
        <label class="range-label" for="reynolds"><span>Reynolds number <span class="inline-keys"><kbd>−</kbd><kbd>+</kbd><kbd>0</kbd></span></span><output>{currentReynolds.toFixed(0)}</output></label>
        <input id="reynolds" type="range" min={Math.log10(50)} max={5} step="0.05" value={Math.log10(currentReynolds)} oninput={(event) => setReynolds(10 ** Number(event.currentTarget.value))} />
        {#if tuning !== null}
          <div class="tuning-control">
            <div><span>{tuningDescription(tuning)}</span><strong>{tuningTitle(tuning)}</strong></div>
            <div class="tuning-buttons">
              <button type="button" aria-label={tuning.id === "pic-flip-blend" ? "More PIC" : "Previous transport method"} aria-keyshortcuts="[" disabled={!tuning.canDecrease} onclick={() => adjustTuning(-1)}><span>{tuning.id === "pic-flip-blend" ? "More PIC" : "Previous"}</span><kbd>[</kbd></button>
              <button type="button" aria-label={tuning.id === "pic-flip-blend" ? "More FLIP" : "Next transport method"} aria-keyshortcuts="]" disabled={!tuning.canIncrease} onclick={() => adjustTuning(1)}><span>{tuning.id === "pic-flip-blend" ? "More FLIP" : "Next"}</span><kbd>]</kbd></button>
            </div>
          </div>
        {:else if solverId === "lbm-d2q9"}
          <div class="static-tuning"><span>Collision model</span><strong>Automatic TRT scaling</strong></div>
        {/if}
      </section>

      <section class="control-section">
        <div class="section-heading"><h2>View</h2><span>presentation only</span></div>
        <div class="toggle-grid">
          <button class:active={snapshot?.vorticityVisible === true} type="button" aria-keyshortcuts="V" onclick={() => client?.send({kind: "toggle-vorticity"})}><span>Vorticity</span><kbd>V</kbd></button>
          <button class:active={snapshot?.tracerMode === "material"} type="button" aria-keyshortcuts="T" onclick={() => client?.send({kind: "toggle-tracers"})}><span>Material tracers</span><kbd>T</kbd></button>
          <button class:active={snapshot?.cropEnabled === true} type="button" aria-keyshortcuts="C" onclick={() => client?.send({kind: "toggle-crop"})}><span>Crop edges</span><kbd>C</kbd></button>
          <button class:active={snapshot?.diagnosticMode === "every-step"} type="button" aria-keyshortcuts="D" onclick={() => client?.send({kind: "toggle-diagnostics"})}><span>Live diagnostics</span><kbd>D</kbd></button>
        </div>
        <span class="field-label trail-blend-label">Trail blending</span>
        <div class="segmented trail-blend-control" aria-label="Trail blending">
          <button class:active={trailBlendMode === "normal"} aria-pressed={trailBlendMode === "normal"} type="button" onclick={() => changeTrailBlendMode("normal")}>Normal</button>
          <button class:active={trailBlendMode === "additive"} aria-pressed={trailBlendMode === "additive"} type="button" onclick={() => changeTrailBlendMode("additive")}>Additive</button>
          <button class:active={trailBlendMode === "screen"} aria-pressed={trailBlendMode === "screen"} type="button" onclick={() => changeTrailBlendMode("screen")}>Screen</button>
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

</main>
