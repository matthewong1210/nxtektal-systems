"use client";

import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";

import {
  createInitialGatewayDemoState,
  gatewayDemoReducer,
} from "../../lib/edge-gateway-model/demo-state";
import {
  GATEWAY_PARTS,
  GATEWAY_PART_BY_ID,
  INSTALLATION_INTERFACES,
  INSTALLATION_INTERFACE_BY_ID,
} from "../../lib/edge-gateway-model/manifest";
import {
  PRESENTATION_DURATION_SECONDS,
  PRESENTATION_SEGMENTS,
} from "../../lib/edge-gateway-model/presentation";
import { resolvePartModel } from "../../lib/edge-gateway-model/model-registry";
import replayExcerpt from "../../lib/edge-gateway-model/fixtures/normal-weekday-inventory-threshold-seed-101.json";
import type {
  CameraPreset,
  FleetDevice,
  FleetDeviceKind,
  GatewayLayer,
  InstallationInterface,
  SceneId,
} from "../../lib/edge-gateway-model/types";

import styles from "./EdgeGatewayDemo.module.css";
import { GatewayCanvas } from "./GatewayCanvas";
import { WebGLFallback } from "./WebGLFallback";

const SCENES: ReadonlyArray<{
  id: SceneId;
  index: string;
  label: string;
  short: string;
}> = [
  { id: "installed-system", index: "01", label: "Installed System", short: "Installed" },
  { id: "exploded-gateway", index: "02", label: "Exploded Gateway", short: "Exploded" },
  { id: "operational-flow", index: "03", label: "Operational Flow", short: "Flow" },
  { id: "scale-the-fleet", index: "04", label: "Scale the Fleet", short: "Fleet" },
  { id: "software-update", index: "05", label: "Software Update", short: "Update" },
  { id: "safety-architecture", index: "06", label: "Safety Architecture", short: "Safety" },
];

const SCENE_DESCRIPTION: Record<SceneId, { eyebrow: string; title: string; body: string }> = {
  "installed-system": {
    eyebrow: "Conceptual installation · planned Pilot configuration",
    title: "One on-site operating layer",
    body: "A procedural engineering model of a wall-mounted Edge Gateway beside existing range equipment. Geometry and routing are approximate.",
  },
  "exploded-gateway": {
    eyebrow: "Service view · approximate geometry",
    title: "Inspectable by component",
    body: "The enclosure, protected power, compute, networking, normal remote I/O, terminations, and optional hardware remain individually selectable.",
  },
  "operational-flow": {
    eyebrow: "User-specified illustrative storyboard · not exported evidence",
    title: "Evidence reaches a human—not an actuator",
    body: "The repository-backed path ends with recorded workflow evidence. A separate simulated replay can show later state without claiming the response caused it.",
  },
  "scale-the-fleet": {
    eyebrow: "Conceptual onboarding workflow · not implemented",
    title: "Keep the Gateway. Register the device.",
    body: "New robot and sensor concepts join through registration, certificates, capabilities, Adapter loading, and commissioning. Camera inference remains on a separate optional node.",
  },
  "software-update": {
    eyebrow: "Conceptual update architecture · simulated sequence",
    title: "Software evolves; hardware remains",
    body: "A signed-release, staged-health-check, and rollback target architecture. The repository does not implement this OTA service today.",
  },
  "safety-architecture": {
    eyebrow: "Conceptual hardware safety chain · not certified",
    title: "The Agent cannot bypass local safety.",
    body: "Operational data and independent emergency-stop power removal are deliberately separated. This is not a final electrical or safety-certified design.",
  },
};

const LAYER_LABEL: Record<GatewayLayer, string> = {
  power: "Power",
  network: "Network",
  telemetry: "Telemetry",
  safety: "Safety",
};

const CAMERA_PRESETS: ReadonlyArray<{ id: CameraPreset; label: string }> = [
  { id: "installed", label: "Reset Camera" },
  { id: "front", label: "Front" },
  { id: "side", label: "Side" },
  { id: "top", label: "Top" },
  { id: "isometric", label: "Isometric" },
];

const INSTALLATION_STATUS = [
  ["Power", "Healthy"],
  ["Primary network", "Connected"],
  ["Cellular backup", "Ready"],
  ["Normal I/O", "Connected"],
  ["Edge runtime", "Healthy"],
  ["Facility State", "Current"],
  ["Cloud sync", "Illustrated"],
] as const;

const FLOW_STEPS = [
  ["01", "Simulated observations", "Synthetic sensor and robot evidence"],
  ["02", "Conceptual Edge host", "Browser-local system representation"],
  ["03", "FacilityState + AssemblyReport", "State and quality evidence remain separate"],
  ["04", "Deterministic evaluation", "Facility advice and Shadow trace stay owner-identified"],
  ["05", "Recommendation + DecisionTrace", "Illustrative storyboard—not canonical output"],
  ["06", "Manager response: ACCEPT", "Workflow evidence recorded; no command issued"],
  ["07", "Physical task admission", "NOT IMPLEMENTED · inactive boundary"],
  ["08", "Separate RangeOps replay", "Seed 101 evidence; not causal proof"],
] as const;

const REPLAY_HANDOFF_FRAME = replayExcerpt.frames[1];

const UPDATE_SEQUENCE = [
  "Signed release manifest illustrated",
  "Signature and image digest verified",
  "Mission, storage, and stable-power checks",
  "Candidate version staged",
  "Service health and state output checked",
  "Activate—or restore retained version",
] as const;

function supportsWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2", { failIfMajorPerformanceCaveat: true }) ||
        canvas.getContext("webgl", { failIfMajorPerformanceCaveat: true }),
    );
  } catch {
    return false;
  }
}

function presentationIndexAt(seconds: number): number {
  const found = PRESENTATION_SEGMENTS.findIndex(
    (segment) => seconds >= segment.startSecond && seconds < segment.endSecond,
  );
  return found === -1 ? PRESENTATION_SEGMENTS.length - 1 : found;
}

function countFleet(
  devices: ReadonlyArray<{ kind: FleetDeviceKind }>,
  kind: FleetDeviceKind,
): number {
  return devices.filter((device) => device.kind === kind).length;
}

function initialModelErrors(): Record<string, string> {
  return Object.fromEntries(
    GATEWAY_PARTS.flatMap((part) => {
      const resolved = resolvePartModel(part.id);
      return resolved.kind === "error" ? [[part.id, resolved.message]] : [];
    }),
  );
}

export function EdgeGatewayDemo({
  initialPresentation,
}: {
  initialPresentation: boolean;
}) {
  const [state, dispatch] = useReducer(
    gatewayDemoReducer,
    undefined,
    createInitialGatewayDemoState,
  );
  const [webGL] = useState(() => supportsWebGL());
  const [documentVisible, setDocumentVisible] = useState(
    () => document.visibilityState === "visible",
  );
  const [reducedMotion, setReducedMotion] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [lowQuality, setLowQuality] = useState(
    () => window.matchMedia("(max-width: 700px), (pointer: coarse)").matches,
  );
  const [presentationActive, setPresentationActive] = useState(initialPresentation);
  const [presentationPaused, setPresentationPaused] = useState(false);
  const [presentationSeconds, setPresentationSeconds] = useState(0);
  const [flowStep, setFlowStep] = useState(1);
  const [updateStep, setUpdateStep] = useState(0);
  const [selectedInterfaceId, setSelectedInterfaceId] = useState<string | null>(null);
  const [presentationRevision, setPresentationRevision] = useState(0);
  const [cameraRevision, setCameraRevision] = useState(0);
  const [modelErrors, setModelErrors] = useState(initialModelErrors);
  const lastPresentationSegment = useRef<string | null>(null);
  const recordModelError = useCallback((partId: string, message: string) => {
    setModelErrors((current) =>
      current[partId] === message ? current : { ...current, [partId]: message },
    );
  }, []);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const coarse = window.matchMedia("(max-width: 700px), (pointer: coarse)");
    const updateMotion = (event: MediaQueryListEvent) => setReducedMotion(event.matches);
    const updateQuality = (event: MediaQueryListEvent) => setLowQuality(event.matches);
    motion.addEventListener("change", updateMotion);
    coarse.addEventListener("change", updateQuality);
    return () => {
      motion.removeEventListener("change", updateMotion);
      coarse.removeEventListener("change", updateQuality);
    };
  }, []);

  useEffect(() => {
    const onVisibility = () => setDocumentVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (
      !presentationActive ||
      presentationPaused ||
      !documentVisible ||
      reducedMotion ||
      presentationSeconds >= PRESENTATION_DURATION_SECONDS
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      setPresentationSeconds((current) =>
        Math.min(PRESENTATION_DURATION_SECONDS, current + 0.25),
      );
    }, 250);
    return () => window.clearInterval(timer);
  }, [
    documentVisible,
    presentationActive,
    presentationPaused,
    presentationSeconds,
    reducedMotion,
  ]);

  const presentationIndex = presentationIndexAt(presentationSeconds);
  const presentationSegment = PRESENTATION_SEGMENTS[presentationIndex];

  useEffect(() => {
    if (!presentationActive || !presentationSegment) return;
    if (lastPresentationSegment.current === presentationSegment.id) return;
    lastPresentationSegment.current = presentationSegment.id;
    queueMicrotask(() => {
      setSelectedInterfaceId(null);
      dispatch({ type: "scene/set", scene: presentationSegment.scene });
      if (presentationSegment.cue !== "explode-components") {
        dispatch({ type: "part/select", partId: null });
      }
      switch (presentationSegment.cue) {
      case "installed-overview":
        dispatch({ type: "gateway/door", open: false });
        dispatch({ type: "gateway/explode", amount: 0 });
          break;
      case "open-enclosure":
        dispatch({ type: "gateway/door", open: true });
          break;
      case "explode-components":
        dispatch({ type: "gateway/door", open: true });
        dispatch({ type: "gateway/explode", amount: 1 });
        dispatch({ type: "part/select", partId: "fanless-edge-computer" });
          break;
      case "illustrative-operational-flow":
        setFlowStep(5);
          break;
      case "record-manager-workflow-evidence":
        dispatch({ type: "manager/record-response", response: "accept" });
        setFlowStep(7);
          break;
      case "conceptual-fleet-onboarding":
        dispatch({ type: "fleet/add", kind: "picker" });
          break;
      case "conceptual-update-success":
        dispatch({ type: "update/start", targetEdgeRuntimeVersion: "0.3.2" });
        dispatch({ type: "update/complete" });
        setUpdateStep(6);
          break;
      case "conceptual-update-rollback":
        dispatch({ type: "update/start", targetEdgeRuntimeVersion: "0.3.3-candidate" });
        dispatch({ type: "update/fail-health-check" });
        setUpdateStep(6);
          break;
      case "independent-safety-path":
        dispatch({ type: "safety/demonstrate-estop", active: true });
          break;
      case "final-overview":
        dispatch({ type: "safety/demonstrate-estop", active: false });
        dispatch({ type: "gateway/door", open: false });
        dispatch({ type: "gateway/explode", amount: 0 });
          break;
      }
      setPresentationRevision((revision) => revision + 1);
    });
  }, [presentationActive, presentationSegment]);

  const selectedPart = state.selectedPartId
    ? GATEWAY_PART_BY_ID.get(state.selectedPartId) ?? null
    : null;
  const selectedInterface = selectedInterfaceId
    ? INSTALLATION_INTERFACE_BY_ID.get(selectedInterfaceId) ?? null
    : null;
  const sceneCopy = SCENE_DESCRIPTION[state.scene];
  const pickerCount = countFleet(state.fleetDevices, "picker");
  const carrierCount = countFleet(state.fleetDevices, "carrier");
  const handoffCount = countFleet(state.fleetDevices, "handoff");
  const sensorCount = countFleet(state.fleetDevices, "sensor");
  const visionNode = state.dedicatedVisionNodeRecommended;
  const totalDevices = state.fleetDevices.length;
  const latestFleetDevice = state.fleetDevices.at(-1) ?? null;
  const visiblePartIndex = GATEWAY_PARTS.filter(
    (part) =>
      !part.optional ||
      state.scene === "exploded-gateway" ||
      (part.id === "vision-node" && visionNode),
  );
  const utilization = {
    cpu: Math.min(42, 16 + totalDevices * 3 + (visionNode ? 2 : 0)),
    memory: Math.min(56, 24 + totalDevices * 2),
    storage: Math.min(44, 12 + totalDevices),
    queue: totalDevices * 3 + 4,
    latency: 18 + totalDevices * 2,
  };

  const jumpPresentation = (direction: -1 | 1) => {
    const next = Math.max(
      0,
      Math.min(PRESENTATION_SEGMENTS.length - 1, presentationIndex + direction),
    );
    setPresentationActive(true);
    setPresentationPaused(true);
    setPresentationSeconds(PRESENTATION_SEGMENTS[next].startSecond);
    lastPresentationSegment.current = null;
  };

  const restartPresentation = () => {
    dispatch({ type: "demo/reset" });
    setFlowStep(1);
    setUpdateStep(0);
    setSelectedInterfaceId(null);
    setPresentationSeconds(0);
    setPresentationActive(true);
    setPresentationPaused(reducedMotion);
    lastPresentationSegment.current = null;
  };

  const chooseScene = (scene: SceneId) => {
    setPresentationActive(false);
    setPresentationPaused(true);
    dispatch({ type: "scene/set", scene });
    dispatch({ type: "part/select", partId: null });
    setSelectedInterfaceId(null);
    if (scene === "exploded-gateway") {
      dispatch({ type: "gateway/door", open: true });
      dispatch({ type: "gateway/explode", amount: Math.max(state.explodeAmount, 0.58) });
    }
  };

  const addFleetDevice = (kind: FleetDeviceKind) => {
    dispatch({ type: "fleet/add", kind });
  };

  const runUpdate = () => {
    dispatch({ type: "update/start", targetEdgeRuntimeVersion: "0.3.2" });
    dispatch({ type: "update/complete" });
    setUpdateStep(6);
  };

  const failUpdate = () => {
    const targetEdgeRuntimeVersion =
      state.update.activeEdgeRuntimeVersion === "0.3.2"
        ? "0.3.3-candidate"
        : "0.3.2";
    dispatch({ type: "update/start", targetEdgeRuntimeVersion });
    dispatch({ type: "update/fail-health-check" });
    setUpdateStep(6);
  };

  return (
    <main className={styles.shell} data-scene={state.scene}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <div>
            <strong>NXTektal</strong>
            <span>Edge Gateway · Digital Twin</span>
          </div>
        </div>
        <div className={styles.headerStatus}>
          <span><i className={styles.liveDot} />Browser local</span>
          <span>Read only</span>
          <span>v0.1 concept</span>
        </div>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={reducedMotion ? styles.activeControl : undefined}
            onClick={() => {
              setReducedMotion((current) => !current);
              setPresentationPaused(true);
            }}
          >
            Reduced motion
          </button>
          <button
            type="button"
            onClick={() => {
              setPresentationActive(true);
              setPresentationPaused(false);
            }}
          >
            Auto presentation
          </button>
        </div>
      </header>

      <div className={styles.truthBanner} role="note">
        <strong>CONCEPTUAL SYSTEM VISUALIZATION — NOT FOR FABRICATION</strong>
        <span>Approximate geometry · planned configuration · no live control</span>
      </div>

      <nav className={styles.sceneTabs} aria-label="Digital twin scenes">
        {SCENES.map((scene) => (
          <button
            key={scene.id}
            type="button"
            role="tab"
            aria-selected={state.scene === scene.id}
            className={state.scene === scene.id ? styles.activeTab : undefined}
            onClick={() => chooseScene(scene.id)}
          >
            <span>{scene.index}</span>
            <strong>{scene.label}</strong>
          </button>
        ))}
      </nav>

      <section className={styles.workspace}>
        <div className={styles.viewerColumn}>
          <div className={styles.viewport}>
            <div className={styles.sceneHeading}>
              <p>{sceneCopy.eyebrow}</p>
              <h1>{sceneCopy.title}</h1>
              <span>{sceneCopy.body}</span>
            </div>

            <div className={styles.viewMeta}>
              <span>UNITS · METERS</span>
              <span>{state.cameraProjection.toUpperCase()}</span>
              <span>{lowQuality ? "MOBILE QUALITY" : "DESKTOP QUALITY"}</span>
            </div>

            {Object.keys(modelErrors).length > 0 ? (
              <div className={styles.modelError} role="alert">
                <strong>REGISTERED MODEL ERROR — PROCEDURAL FALLBACK DISABLED</strong>
                {Object.entries(modelErrors).map(([partId, message]) => (
                  <span key={partId}>{message}</span>
                ))}
              </div>
            ) : null}

            {webGL === false ? (
              <WebGLFallback parts={GATEWAY_PARTS} />
            ) : (
              <GatewayCanvas
                key={`${state.scene}-${presentationRevision}-${cameraRevision}`}
                scene={state.scene}
                parts={GATEWAY_PARTS}
                selectedPartId={state.selectedPartId}
                onSelectPart={(partId) => {
                  setSelectedInterfaceId(null);
                  dispatch({ type: "part/select", partId: partId || null });
                }}
                selectedInterfaceId={selectedInterfaceId}
                onSelectInterface={(interfaceId) => {
                  setSelectedInterfaceId(interfaceId);
                  dispatch({ type: "part/select", partId: null });
                }}
                explodeAmount={state.explodeAmount}
                doorOpen={state.doorOpen}
                transparent={state.transparentEnclosure}
                cutaway={state.cutaway}
                showDimensions={state.showDimensions}
                layers={state.layerVisibility}
                cameraPreset={state.cameraPreset}
                cameraMode={state.cameraProjection}
                pickerCount={pickerCount}
                carrierCount={carrierCount}
                handoffCount={handoffCount}
                sensorCount={sensorCount}
                visionNode={visionNode}
                flowStep={flowStep}
                updateStep={updateStep}
                updateFailed={state.update.phase === "rolled-back"}
                reducedMotion={reducedMotion}
                paused={presentationActive && presentationPaused}
                documentVisible={documentVisible}
                lowQuality={lowQuality}
                onModelError={recordModelError}
              />
            )}

            <div className={styles.viewportChrome} aria-hidden="true">
              <i className={styles.cornerTl} />
              <i className={styles.cornerTr} />
              <i className={styles.cornerBl} />
              <i className={styles.cornerBr} />
            </div>

            {state.showLabels && state.scene === "installed-system" ? (
              <div className={styles.interfaceLabels} aria-label="Conceptual installation interfaces">
                {INSTALLATION_INTERFACES.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    data-interface={item.id}
                    aria-pressed={selectedInterfaceId === item.id}
                    onClick={() => {
                      setSelectedInterfaceId(item.id);
                      dispatch({ type: "part/select", partId: null });
                    }}
                  >
                    {item.label}
                  </button>
                ))}
                <button
                  type="button"
                  data-interface="ground-bond"
                  aria-pressed={state.selectedPartId === "structured-wiring"}
                  onClick={() => {
                    setSelectedInterfaceId(null);
                    dispatch({ type: "part/select", partId: "structured-wiring" });
                  }}
                >
                  PE bond
                </button>
              </div>
            ) : null}

            {state.showLabels && state.scene === "operational-flow" ? (
              <div className={styles.flowMapLabels} aria-label="Operational flow 3D site labels">
                <div><span>Dispenser sensor</span><span>Washer</span><span>Picker R1</span><span>Picker R2</span><span>Carrier C1</span><span>Universal Handoff H1</span></div>
                <div><strong>Edge Gateway</strong><strong>NXTektal Cloud</strong><strong>Manager tablet</strong></div>
                <small>Robots retain local navigation · obstacle avoidance · motor and mechanism control · local safety stop</small>
              </div>
            ) : null}

            {state.showLabels && state.scene !== "operational-flow" && state.scene !== "installed-system" ? (
              <div className={styles.sceneLabels} aria-hidden="true">
                <span className={styles.labelA}>EDGE HOST</span>
                <span className={styles.labelB}>POWER</span>
                <span className={styles.labelC}>NETWORK + I/O</span>
                <span className={styles.labelGround}>PE BOND</span>
              </div>
            ) : null}

            {webGL !== false ? (
              <SceneOverlay
                scene={state.scene}
                flowStep={flowStep}
                setFlowStep={setFlowStep}
                managerRecorded={state.managerWorkflow.recorded}
                onRecordManager={() => {
                  dispatch({ type: "manager/record-response", response: "accept" });
                  setFlowStep(6);
                }}
                fleetCount={totalDevices}
                latestFleetDevice={latestFleetDevice}
                utilization={utilization}
                visionNode={visionNode}
                updateStep={updateStep}
                updateFailed={state.update.phase === "rolled-back"}
                updateActivated={state.update.phase === "activated"}
                activeUpdateVersion={state.update.activeEdgeRuntimeVersion}
                safetyActive={state.safetyPath.demonstrationActive}
              />
            ) : null}
          </div>

          <div className={styles.controlDeck}>
            <div className={styles.navigationGuide} aria-label="Viewport navigation">
              <span>Drag · Rotate</span>
              <span>Shift + drag · Pan</span>
              <span>Wheel / pinch · Zoom</span>
              <span>Arrow keys · Orbit</span>
            </div>
            <div className={styles.controlGroup} aria-label="Camera controls">
              <span>CAMERA</span>
              <button
                type="button"
                className={state.cameraProjection === "perspective" ? styles.activeControl : undefined}
                onClick={() => dispatch({ type: "camera/projection", projection: "perspective" })}
              >
                Perspective
              </button>
              <button
                type="button"
                className={state.cameraProjection === "orthographic" ? styles.activeControl : undefined}
                onClick={() => dispatch({ type: "camera/projection", projection: "orthographic" })}
              >
                Orthographic
              </button>
              {CAMERA_PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  className={state.cameraPreset === preset.id ? styles.activeControl : undefined}
                  onClick={() => {
                    dispatch({ type: "camera/preset", preset: preset.id });
                    setCameraRevision((current) => current + 1);
                  }}
                >
                  {preset.label}
                </button>
              ))}
              <button
                type="button"
                onClick={() => {
                  dispatch({ type: "camera/preset", preset: "installed" });
                  setCameraRevision((current) => current + 1);
                }}
              >
                Installed View
              </button>
            </div>
            <div className={styles.controlGroup} aria-label="Layer visibility">
              <span>LAYERS</span>
              {Object.entries(LAYER_LABEL).map(([layer, label]) => {
                const id = layer as GatewayLayer;
                return (
                  <button
                    key={id}
                    type="button"
                    aria-label={`Show ${id}`}
                    aria-pressed={state.layerVisibility[id]}
                    className={state.layerVisibility[id] ? styles.activeControl : undefined}
                    onClick={() =>
                      dispatch({
                        type: "layer/set",
                        layer: id,
                        visible: !state.layerVisibility[id],
                      })
                    }
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <aside className={styles.inspector} aria-label="Engineering inspector">
          <div className={styles.inspectorHeader}>
            <div>
              <span>ENGINEERING INSPECTOR</span>
              <strong>{selectedPart ? "COMPONENT" : selectedInterface ? "INTERFACE" : "SCENE"}</strong>
            </div>
            <span className={styles.healthPill}>● SIMULATED</span>
          </div>

          {selectedPart ? (
            <ComponentInspector
              part={selectedPart}
              onClose={() => dispatch({ type: "part/select", partId: null })}
            />
          ) : selectedInterface ? (
            <InstallationInterfaceInspector
              item={selectedInterface}
              onClose={() => setSelectedInterfaceId(null)}
            />
          ) : (
            <SceneInspector
              scene={state.scene}
              explodeAmount={state.explodeAmount}
              doorOpen={state.doorOpen}
              transparent={state.transparentEnclosure}
              cutaway={state.cutaway}
              showDimensions={state.showDimensions}
              showLabels={state.showLabels}
              onExplode={(amount) => dispatch({ type: "gateway/explode", amount })}
              onDoor={() => dispatch({ type: "gateway/door", open: !state.doorOpen })}
              onTransparent={() => dispatch({ type: "gateway/transparency", enabled: !state.transparentEnclosure })}
              onCutaway={() => dispatch({ type: "gateway/cutaway", enabled: !state.cutaway })}
              onDimensions={() => dispatch({ type: "gateway/dimensions", visible: !state.showDimensions })}
              onLabels={() => dispatch({ type: "gateway/labels", visible: !state.showLabels })}
              addFleetDevice={addFleetDevice}
              requestVision={() => dispatch({ type: "fleet/request-camera-workload" })}
              runUpdate={runUpdate}
              failUpdate={failUpdate}
              safetyActive={state.safetyPath.demonstrationActive}
              toggleSafety={() => dispatch({ type: "safety/demonstrate-estop", active: !state.safetyPath.demonstrationActive })}
            />
          )}

          <div className={styles.partBrowser}>
            <div className={styles.sectionLabel}>
              <span>COMPONENT INDEX</span>
              <small>{visiblePartIndex.length} visible · {GATEWAY_PARTS.length} total</small>
            </div>
            <div className={styles.partGrid}>
              {visiblePartIndex.map((part) => (
                <button
                  type="button"
                  key={part.id}
                  className={state.selectedPartId === part.id ? styles.selectedPart : undefined}
                  onClick={() => {
                    setSelectedInterfaceId(null);
                    dispatch({ type: "part/select", partId: part.id });
                  }}
                >
                  <i data-category={part.category} />
                  <span>{part.label}</span>
                  <small>{part.category}</small>
                </button>
              ))}
            </div>
          </div>
        </aside>
      </section>

      <section className={styles.presentationBar} aria-label="Presentation timeline">
        <div className={styles.presentationControls}>
          <button type="button" aria-label="Previous step" onClick={() => jumpPresentation(-1)}>‹</button>
          <button
            type="button"
            aria-label={presentationPaused ? "Play presentation" : "Pause presentation"}
            aria-pressed={presentationPaused}
            onClick={() => {
              setPresentationActive(true);
              setPresentationPaused((current) => !current);
            }}
          >
            {presentationPaused ? "Play" : "Pause"}
          </button>
          <button type="button" aria-label="Next step" onClick={() => jumpPresentation(1)}>›</button>
          <button type="button" aria-label="Restart presentation" onClick={restartPresentation}>Restart</button>
        </div>
        <div className={styles.timeline}>
          {PRESENTATION_SEGMENTS.map((segment, index) => (
            <button
              key={segment.id}
              type="button"
              className={index === presentationIndex && presentationActive ? styles.activeTimeline : undefined}
              onClick={() => {
                setPresentationActive(true);
                setPresentationPaused(true);
                setPresentationSeconds(segment.startSecond);
                lastPresentationSegment.current = null;
              }}
            >
              <i />
              <span>{segment.title}</span>
            </button>
          ))}
          <div
            className={styles.timelineProgress}
            style={{ width: `${(presentationSeconds / PRESENTATION_DURATION_SECONDS) * 100}%` }}
          />
        </div>
        <div className={styles.timecode}>
          <strong>{String(Math.floor(presentationSeconds / 60)).padStart(2, "0")}:{String(Math.floor(presentationSeconds % 60)).padStart(2, "0")}</strong>
          <span>/ 01:15</span>
        </div>
      </section>

      <footer className={styles.footer}>
        <span>SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA</span>
        <strong>An updatable on-site operating layer for autonomous golf facilities.</strong>
        <span>NO MANUFACTURING · CERTIFICATION · DEPLOYMENT CLAIM</span>
      </footer>

      <details className={styles.screenReaderParts}>
        <summary>Accessible list of all conceptual components</summary>
        <ul>
          {GATEWAY_PARTS.map((part) => (
            <li key={part.id}>
              {part.label}: approximate {part.approximateDimensionsMm.join(" by ")} millimeters. {part.description}
            </li>
          ))}
        </ul>
      </details>
    </main>
  );
}

function SceneOverlay({
  scene,
  flowStep,
  setFlowStep,
  managerRecorded,
  onRecordManager,
  fleetCount,
  latestFleetDevice,
  utilization,
  visionNode,
  updateStep,
  updateFailed,
  updateActivated,
  activeUpdateVersion,
  safetyActive,
}: {
  scene: SceneId;
  flowStep: number;
  setFlowStep: (step: number | ((current: number) => number)) => void;
  managerRecorded: boolean;
  onRecordManager: () => void;
  fleetCount: number;
  latestFleetDevice: FleetDevice | null;
  utilization: { cpu: number; memory: number; storage: number; queue: number; latency: number };
  visionNode: boolean;
  updateStep: number;
  updateFailed: boolean;
  updateActivated: boolean;
  activeUpdateVersion: string;
  safetyActive: boolean;
}) {
  if (scene === "installed-system") {
    return (
      <div className={`${styles.sceneCard} ${styles.installedCard}`}>
        <div className={styles.cardTitle}><span>INSTALLATION STATUS</span><strong>PILOT COURSE A · SIMULATED</strong></div>
        <ul className={styles.statusList}>
          {INSTALLATION_STATUS.map(([label, value]) => (
            <li key={label}><span>{label}</span><strong><i />{value}</strong></li>
          ))}
        </ul>
      </div>
    );
  }
  if (scene === "operational-flow") {
    return (
      <div className={`${styles.sceneCard} ${styles.flowCard}`}>
        <div className={styles.cardTitle}>
          <span>PILOT COURSE A · ILLUSTRATIVE VALUES</span>
          <strong>17:20 · EVENING DEMAND STORYBOARD</strong>
        </div>
        <div className={styles.flowReadout}>
          <div><span>Dispenser</span><strong>38%</strong></div>
          <div><span>Demand</span><strong>1.8×</strong></div>
          <div><span>R1 battery</span><strong>72%</strong></div>
          <div><span>Quality</span><strong>Illustrative</strong></div>
        </div>
        <ol className={styles.flowList}>
          {FLOW_STEPS.map(([index, title, detail], stepIndex) => (
            <li
              key={index}
              className={
                stepIndex === 6
                  ? styles.inactiveFlow
                  : stepIndex < flowStep
                    ? styles.completeFlow
                    : stepIndex === flowStep
                      ? styles.currentFlow
                      : undefined
              }
            >
              <span>{index}</span><div><strong>{title}</strong><small>{detail}</small></div>
            </li>
          ))}
        </ol>
        {flowStep >= 7 ? (
          <div className={styles.replayEvidence}>
            <div><span>SEPARATE RANGEOPS REPLAY</span><strong>SEED {replayExcerpt.source.seed} · {replayExcerpt.source.policy}</strong></div>
            <dl>
              <div><dt>Recorded directive</dt><dd>{REPLAY_HANDOFF_FRAME.directive}</dd></div>
              <div><dt>SafetyShield</dt><dd>{REPLAY_HANDOFF_FRAME.safetyShieldAllowed ? "allowed" : "rejected"}</dd></div>
              <div><dt>Recorded state</dt><dd>{REPLAY_HANDOFF_FRAME.robotId} · {REPLAY_HANDOFF_FRAME.robotActivity} · {REPLAY_HANDOFF_FRAME.robotLocation}</dd></div>
            </dl>
            <small>Deterministic simulator evidence from commit {replayExcerpt.source.gitCommit}; not caused by the manager response above.</small>
          </div>
        ) : null}
        <div className={styles.cardActions}>
          <button type="button" aria-label="Record manager response" disabled={managerRecorded} onClick={onRecordManager}>
            {managerRecorded ? "ACCEPT recorded · no command" : "Record response · ACCEPT"}
          </button>
          <button type="button" aria-label="Next step" onClick={() => setFlowStep((current) => Math.min(FLOW_STEPS.length - 1, current + 1))}>Next step</button>
        </div>
      </div>
    );
  }
  if (scene === "scale-the-fleet") {
    return (
      <div className={styles.sceneCard} data-evidence-card="fleet">
        <div className={styles.cardTitle}><span>CONCEPTUAL ONBOARDING</span><strong>SAME GATEWAY · {fleetCount} DEVICES</strong></div>
        <p className={styles.callout}>Same Gateway — new device registration and Adapter</p>
        {visionNode ? <p className={styles.visionCallout}>Dedicated Vision Node Recommended</p> : null}
        {latestFleetDevice ? (
          <div className={styles.onboardingEvidence}>
            <div><span>LATEST CONCEPT DEVICE</span><strong>{latestFleetDevice.label}</strong></div>
            <ol>
              {latestFleetDevice.onboarding.map((step) => (
                <li key={step.id}>✓ {step.label}</li>
              ))}
            </ol>
            <p>{latestFleetDevice.capabilities.join(" · ")}</p>
            <small>Illustrated target sequence only · not connected to a live facility</small>
          </div>
        ) : (
          <p className={styles.onboardingEmpty}>Add a conceptual device to inspect registration, certificate, capability, Adapter, and commissioning steps.</p>
        )}
        <div className={styles.meters}>
          {[
            ["CPU", utilization.cpu, "%"],
            ["MEM", utilization.memory, "%"],
            ["STORAGE", utilization.storage, "%"],
          ].map(([label, value, suffix]) => (
            <div key={label}><span>{label}</span><i><b style={{ width: `${value}%` }} /></i><strong>{value}{suffix}</strong></div>
          ))}
        </div>
        <div className={styles.metricRow}><span>Telemetry queue · {utilization.queue} msg</span><span>Evaluation latency · {utilization.latency} ms</span><em>SIMULATED</em></div>
      </div>
    );
  }
  if (scene === "software-update") {
    return (
      <div className={styles.sceneCard} data-evidence-card="update">
        <div className={styles.cardTitle}><span>CONCEPTUAL OTA SEQUENCE</span><strong>{updateFailed ? `ROLLBACK COMPLETE · ${activeUpdateVersion}` : updateActivated ? `HEALTHY · ${activeUpdateVersion}` : `READY · ${activeUpdateVersion}`}</strong></div>
        <ol className={styles.updateList}>
          {UPDATE_SEQUENCE.map((step, index) => <li key={step} className={index < updateStep ? styles.completeUpdate : undefined}><i>{index < updateStep ? "✓" : index + 1}</i><span>{step}</span></li>)}
        </ol>
        <p className={updateFailed ? styles.rollback : styles.updateTruth}>
          {updateFailed ? `Failed health check → retained version ${activeUpdateVersion} restored → rollback report recorded` : "Agent updates normally change software, not Gateway hardware."}
        </p>
      </div>
    );
  }
  if (scene === "safety-architecture") {
    return (
      <div className={styles.sceneCard} data-evidence-card="safety">
        <div className={styles.cardTitle}><span>INDEPENDENT SAFETY PATH</span><strong>{safetyActive ? "PATH EMPHASIZED" : "CONCEPT VIEW"}</strong></div>
        <div className={styles.safetyPaths}>
          <div><span>OPERATING DATA</span><p>Agent → manager response → inactive future admission gap → local controller</p></div>
          <div><span>PHYSICAL SAFETY</span><p>Emergency Stop → safety relay / robot safety controller → motor and mechanism power</p></div>
        </div>
        <strong className={styles.safetyStatement}>The Agent cannot bypass local safety.</strong>
      </div>
    );
  }
  return null;
}

function InstallationInterfaceInspector({
  item,
  onClose,
}: {
  item: InstallationInterface;
  onClose: () => void;
}) {
  return (
    <div className={styles.inspectorBody}>
      <button type="button" className={styles.closeInspector} onClick={onClose} aria-label="Close installation interface inspector">×</button>
      <p className={styles.eyebrow}>conceptual service-area interface</p>
      <h2>{item.label}</h2>
      <p>{item.description}</p>
      <dl className={styles.specList}>
        <div><dt>Approx. envelope</dt><dd>{item.approximateDimensionsMm.join(" × ")} mm</dd></div>
        <div><dt>Source</dt><dd>User-requested illustrative context</dd></div>
        <div><dt>Commissioning</dt><dd>Not a surveyed fact</dd></div>
        <div><dt>Control</dt><dd>Unavailable</dd></div>
      </dl>
      <p className={styles.warningCallout}>Presentation anchor only · not live equipment, a deployment record, or fabrication geometry.</p>
    </div>
  );
}

function ComponentInspector({
  part,
  onClose,
}: {
  part: (typeof GATEWAY_PARTS)[number];
  onClose: () => void;
}) {
  return (
    <div className={styles.inspectorBody}>
      <button type="button" className={styles.closeInspector} onClick={onClose} aria-label="Close component inspector">×</button>
      <p className={styles.eyebrow}>{part.category} · {part.status.replaceAll("-", " ")}</p>
      <h2>{part.label}</h2>
      <p>{part.description}</p>
      <dl className={styles.specList}>
        <div><dt>Approx. dimensions</dt><dd>{part.approximateDimensionsMm.join(" × ")} mm</dd></div>
        <div><dt>Geometry</dt><dd>Procedural v1</dd></div>
        <div><dt>Health</dt><dd><i className={styles.liveDot} />Simulated healthy</dd></div>
        <div><dt>Fabrication</dt><dd>Not permitted</dd></div>
      </dl>
      {part.id === "fanless-edge-computer" ? (
        <div className={styles.configBlock}>
          <span>CONCEPTUAL PILOT CONFIGURATION</span>
          <ul><li>x86 fanless computer</li><li>32 GB RAM · 1 TB NVMe</li><li>TPM 2.0 · Ubuntu</li><li>No GPU required for initial Pilot</li></ul>
          <small>Physical adapters, live service operation, cloud synchronization, and robot-command admission are not implemented.</small>
        </div>
      ) : null}
      {part.id === "remote-io-module" ? <p className={styles.warningCallout}>Not part of the emergency-stop safety chain.</p> : null}
      <div className={styles.connectionList}>
        <span>CONCEPTUAL CONNECTIONS</span>
        {part.connections.map((connection) => <div key={`${connection.targetId}-${connection.kind}`}><i data-kind={connection.kind} /><p><strong>{connection.kind}</strong><small>{connection.label}</small></p></div>)}
      </div>
    </div>
  );
}

function SceneInspector({
  scene,
  explodeAmount,
  doorOpen,
  transparent,
  cutaway,
  showDimensions,
  showLabels,
  onExplode,
  onDoor,
  onTransparent,
  onCutaway,
  onDimensions,
  onLabels,
  addFleetDevice,
  requestVision,
  runUpdate,
  failUpdate,
  safetyActive,
  toggleSafety,
}: {
  scene: SceneId;
  explodeAmount: number;
  doorOpen: boolean;
  transparent: boolean;
  cutaway: boolean;
  showDimensions: boolean;
  showLabels: boolean;
  onExplode: (amount: number) => void;
  onDoor: () => void;
  onTransparent: () => void;
  onCutaway: () => void;
  onDimensions: () => void;
  onLabels: () => void;
  addFleetDevice: (kind: FleetDeviceKind) => void;
  requestVision: () => void;
  runUpdate: () => void;
  failUpdate: () => void;
  safetyActive: boolean;
  toggleSafety: () => void;
}) {
  return (
    <div className={styles.inspectorBody}>
      <p className={styles.eyebrow}>{SCENE_DESCRIPTION[scene].eyebrow}</p>
      <h2>{SCENE_DESCRIPTION[scene].title}</h2>
      <p>{SCENE_DESCRIPTION[scene].body}</p>

      {(scene === "installed-system" || scene === "exploded-gateway") ? (
        <>
          <label className={styles.rangeControl}>
            <span><strong>Explode</strong><em>{Math.round(explodeAmount * 100)}%</em></span>
            <input aria-label="Explode" type="range" min="0" max="100" value={Math.round(explodeAmount * 100)} onChange={(event) => onExplode(Number(event.target.value) / 100)} />
          </label>
          <div className={styles.toggleGrid}>
            <button type="button" aria-pressed={doorOpen} onClick={onDoor}>{doorOpen ? "Close enclosure" : "Open enclosure"}</button>
            <button type="button" aria-pressed={transparent} onClick={onTransparent}>Transparent enclosure</button>
            <button type="button" aria-pressed={cutaway} onClick={onCutaway}>Cutaway</button>
            <button type="button" aria-pressed={showDimensions} onClick={onDimensions}>Show dimensions</button>
            <button type="button" aria-pressed={showLabels} onClick={onLabels}>Show labels</button>
          </div>
        </>
      ) : null}

      {scene === "operational-flow" ? (
        <div className={styles.boundaryCard}>
          <span>IMPLEMENTED SOFTWARE BOUNDARY</span>
          <p>State + separate quality evidence → owner-identified advice and trace → immutable manager workflow record.</p>
          <strong>STOP · NO COMMAND ISSUED</strong>
          <small>Physical command admission and typed site-task translation: NOT IMPLEMENTED.</small>
        </div>
      ) : null}

      {scene === "scale-the-fleet" ? (
        <div className={styles.actionStack}>
          <span>ADD CONCEPTUAL DEVICE</span>
          <button type="button" aria-label="Add Picker" onClick={() => addFleetDevice("picker")}>＋ Add Picker</button>
          <button type="button" aria-label="Add Carrier" onClick={() => addFleetDevice("carrier")}>＋ Add Carrier</button>
          <button type="button" aria-label="Add Handoff" onClick={() => addFleetDevice("handoff")}>＋ Add Handoff</button>
          <button type="button" aria-label="Add Sensor" onClick={() => addFleetDevice("sensor")}>＋ Add Sensor</button>
          <button type="button" aria-label="Add Vision Node" onClick={requestVision}>＋ Add Vision Node / camera workload</button>
          <small>Registration, certificates, Adapter loading, runtime onboarding, and utilization values are conceptual—not implemented benchmarks.</small>
        </div>
      ) : null}

      {scene === "software-update" ? (
        <div className={styles.actionStack}>
          <span>UPDATE REHEARSAL</span>
          <button type="button" aria-label="Run update" onClick={runUpdate}>Run update · target 0.3.2</button>
          <button type="button" aria-label="Simulate Failed Health Check" onClick={failUpdate}>Simulate Failed Health Check</button>
          <dl className={styles.versionList}><div><dt>Edge Runtime</dt><dd>0.3.1 → 0.3.2</dd></div><div><dt>Policy</dt><dd>0.1.5</dd></div><div><dt>Site Configuration</dt><dd>1.4</dd></div><div><dt>Carrier Adapter</dt><dd>0.2.0</dd></div></dl>
        </div>
      ) : null}

      {scene === "safety-architecture" ? (
        <div className={styles.actionStack}>
          <span>SAFETY LAYER</span>
          <button type="button" aria-pressed={safetyActive} onClick={toggleSafety}>{safetyActive ? "Hide emergency-stop path" : "Show emergency-stop path"}</button>
          <small>The conceptual path does not pass through Cloud, Agent, Edge Gateway policy, LLM, standard remote I/O, or the manager dashboard.</small>
        </div>
      ) : null}

      <dl className={styles.sceneFacts}>
        <div><dt>World scale</dt><dd>1 unit = 1 meter</dd></div>
        <div>
          <dt>Data source</dt>
          <dd>
            {scene === "operational-flow"
              ? `Illustrative story + RangeOps seed ${replayExcerpt.source.seed}`
              : "Fixed illustrative view-model"}
          </dd>
        </div>
        <div><dt>Runtime</dt><dd>Browser-local / read-only</dd></div>
        <div><dt>External calls</dt><dd>None</dd></div>
      </dl>
    </div>
  );
}
