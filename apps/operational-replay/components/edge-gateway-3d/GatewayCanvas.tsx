"use client";

import {
  Canvas,
  type ThreeEvent,
  useFrame,
  useLoader,
  useThree,
} from "@react-three/fiber";
import {
  Component,
  Suspense,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
} from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import {
  dimensionsMeters,
  INSTALLATION_INTERFACES,
  LOAD_CELL_ASSEMBLY_GEOMETRY,
} from "../../lib/edge-gateway-model/manifest";

import {
  registeredModelLoadError,
  resolvePartModel,
  validateLoadedModelDimensions,
} from "../../lib/edge-gateway-model/model-registry";

import type {
  CameraPreset,
  GatewayPart,
  InstallationInterface,
  ResolvedModel,
  SceneId,
} from "../../lib/edge-gateway-model/types";

type LayerVisibility = Record<"power" | "network" | "telemetry" | "safety", boolean>;

export type GatewayCanvasProps = {
  scene: SceneId;
  parts: readonly GatewayPart[];
  selectedPartId: string | null;
  onSelectPart: (partId: string) => void;
  selectedInterfaceId: string | null;
  onSelectInterface: (interfaceId: string) => void;
  explodeAmount: number;
  doorOpen: boolean;
  transparent: boolean;
  cutaway: boolean;
  showDimensions: boolean;
  layers: LayerVisibility;
  cameraPreset: CameraPreset;
  cameraMode: "perspective" | "orthographic";
  pickerCount: number;
  carrierCount: number;
  handoffCount: number;
  sensorCount: number;
  visionNode: boolean;
  flowStep: number;
  updateStep: number;
  updateFailed: boolean;
  reducedMotion: boolean;
  paused: boolean;
  documentVisible: boolean;
  lowQuality: boolean;
  onModelError: (partId: string, message: string) => void;
};

const COLORS = {
  graphite: "#151d1a",
  graphiteLight: "#2b3531",
  metal: "#6d7771",
  edge: "#b5c2bc",
  green: "#63f59c",
  greenDim: "#23583a",
  amber: "#f1b84b",
  red: "#ff625d",
  white: "#edf5f0",
  blue: "#7bb8d9",
};

type CadBoxProps = {
  dimensions: readonly [number, number, number];
  position?: readonly [number, number, number];
  rotation?: readonly [number, number, number];
  color?: string;
  opacity?: number;
  selected?: boolean;
  dimmed?: boolean;
  emissive?: string;
  onClick?: (event: ThreeEvent<MouseEvent>) => void;
};

function CadBox({
  dimensions,
  position = [0, 0, 0],
  rotation = [0, 0, 0],
  color = COLORS.graphiteLight,
  opacity = 1,
  selected = false,
  dimmed = false,
  emissive = "#000000",
  onClick,
}: CadBoxProps) {
  const geometry = useMemo(
    () => new THREE.BoxGeometry(dimensions[0], dimensions[1], dimensions[2]),
    [dimensions],
  );
  const edges = useMemo(() => new THREE.EdgesGeometry(geometry, 24), [geometry]);

  useEffect(
    () => () => {
      edges.dispose();
      geometry.dispose();
    },
    [edges, geometry],
  );

  const effectiveOpacity = Math.max(0.1, opacity * (dimmed ? 0.23 : 1));
  return (
    <group position={position} rotation={rotation}>
      <mesh geometry={geometry} onClick={onClick} castShadow receiveShadow>
        <meshStandardMaterial
          color={color}
          emissive={selected ? COLORS.green : emissive}
          emissiveIntensity={selected ? 0.34 : 0.08}
          metalness={0.38}
          roughness={0.58}
          transparent={effectiveOpacity < 1}
          opacity={effectiveOpacity}
          depthWrite={effectiveOpacity > 0.4}
        />
      </mesh>
      <lineSegments geometry={edges}>
        <lineBasicMaterial
          color={selected ? COLORS.green : COLORS.edge}
          transparent
          opacity={selected ? 1 : dimmed ? 0.12 : 0.48}
        />
      </lineSegments>
    </group>
  );
}

function SegmentLine({
  points,
  color,
  opacity = 0.72,
  dashed = false,
}: {
  points: readonly (readonly [number, number, number])[];
  color: string;
  opacity?: number;
  dashed?: boolean;
}) {
  const geometry = useMemo(
    () =>
      new THREE.BufferGeometry().setFromPoints(
        points.map((point) => new THREE.Vector3(...point)),
      ),
    [points],
  );
  const material = useMemo(
    () =>
      dashed
        ? new THREE.LineDashedMaterial({
            color,
            dashSize: 0.09,
            gapSize: 0.06,
            transparent: true,
            opacity,
          })
        : new THREE.LineBasicMaterial({ color, transparent: true, opacity }),
    [color, dashed, opacity],
  );
  const line = useMemo(() => {
    const instance = new THREE.Line(geometry, material);
    if (dashed) instance.computeLineDistances();
    return instance;
  }, [dashed, geometry, material]);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  return <primitive object={line} />;
}

class GatewayGLTFLoader extends GLTFLoader {
  constructor() {
    const manager = new THREE.LoadingManager();
    super(manager);
    manager.setURLModifier((resourceUrl) => {
      if (resourceUrl.startsWith("data:")) return resourceUrl;
      const candidate = new URL(resourceUrl, window.location.href);
      if (candidate.protocol === "blob:" && candidate.origin === window.location.origin) {
        return resourceUrl;
      }
      if (
        candidate.origin !== window.location.origin ||
        !candidate.pathname.startsWith("/models/edge-gateway/") ||
        candidate.username ||
        candidate.password ||
        candidate.search ||
        candidate.hash
      ) {
        throw new Error(
          `model resource must remain same-origin under /models/edge-gateway/: ${candidate.pathname}`,
        );
      }
      return candidate.href;
    });
  }
}

type RegisteredModel = Extract<ResolvedModel, { kind: "glb" }>;

function RegisteredGatewayPart({
  model,
  part,
  position,
  selected,
  dimmed,
  onSelect,
}: {
  model: RegisteredModel;
  part: GatewayPart;
  position: readonly [number, number, number];
  selected: boolean;
  dimmed: boolean;
  onSelect: (partId: string) => void;
}) {
  const gltf = useLoader(GatewayGLTFLoader, model.sourcePath);
  const loaded = useMemo(() => {
    const scene = gltf.scene.clone(true);
    scene.name = model.componentId;
    scene.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(scene);
    if (bounds.isEmpty()) {
      throw new Error("model has no renderable bounds");
    }
    const dimensions = bounds.getSize(new THREE.Vector3());
    const dimensionsValidation = validateLoadedModelDimensions(
      part.id,
      dimensions.toArray(),
    );
    if (!dimensionsValidation.valid) {
      throw new Error(dimensionsValidation.message);
    }

    const ownedMaterials: THREE.Material[] = [];
    scene.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return;
      const materials = Array.isArray(object.material)
        ? object.material
        : [object.material];
      const replacements = materials.map((material) => {
        const replacement = material.clone();
        replacement.transparent = dimmed || replacement.transparent;
        replacement.opacity = dimmed
          ? Math.min(replacement.opacity, 0.22)
          : replacement.opacity;
        if (selected && "emissive" in replacement) {
          const withEmissive = replacement as THREE.MeshStandardMaterial;
          withEmissive.emissive.set(COLORS.green);
          withEmissive.emissiveIntensity = 0.25;
        }
        ownedMaterials.push(replacement);
        return replacement;
      });
      object.material = Array.isArray(object.material)
        ? replacements
        : replacements[0];
    });
    return { scene, ownedMaterials };
  }, [dimmed, gltf.scene, model.componentId, part, selected]);

  useEffect(
    () => () => loaded.ownedMaterials.forEach((material) => material.dispose()),
    [loaded],
  );

  return (
    <group
      position={position}
      onClick={(event: ThreeEvent<MouseEvent>) => {
        event.stopPropagation();
        onSelect(part.id);
      }}
    >
      <primitive object={loaded.scene} dispose={null} />
    </group>
  );
}

function ModelErrorMarker({
  part,
  position,
}: {
  part: GatewayPart;
  position: readonly [number, number, number];
}) {
  const dimensions = part.approximateDimensionsMm.map(
    (value) => value / 1_000,
  ) as [number, number, number];
  return (
    <group position={position}>
      <CadBox
        dimensions={dimensions}
        color={COLORS.red}
        opacity={0.18}
        emissive={COLORS.red}
      />
      <mesh>
        <octahedronGeometry args={[Math.max(...dimensions) * 0.24]} />
        <meshBasicMaterial color={COLORS.red} wireframe />
      </mesh>
    </group>
  );
}

function RegisteredModelLoadingMarker({
  part,
  position,
}: {
  part: GatewayPart;
  position: readonly [number, number, number];
}) {
  const dimensions = part.approximateDimensionsMm.map(
    (value) => value / 1_000,
  ) as [number, number, number];
  return (
    <group name={`Loading registered model ${part.id}`} position={position}>
      <CadBox
        dimensions={dimensions}
        color={COLORS.amber}
        opacity={0.12}
        emissive={COLORS.amber}
      />
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[Math.max(...dimensions) * 0.2, 0.008, 8, 20]} />
        <meshBasicMaterial color={COLORS.amber} wireframe />
      </mesh>
    </group>
  );
}

class ModelErrorBoundary extends Component<
  {
    children: ReactNode;
    part: GatewayPart;
    position: readonly [number, number, number];
    onError: (partId: string, message: string) => void;
  },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    const failure = registeredModelLoadError(this.props.part.id, error.message);
    this.props.onError(this.props.part.id, failure.message);
  }

  render() {
    return this.state.failed ? (
      <ModelErrorMarker part={this.props.part} position={this.props.position} />
    ) : (
      this.props.children
    );
  }
}

function StatusLed({
  position,
  color = COLORS.green,
}: {
  position: readonly [number, number, number];
  color?: string;
}) {
  return (
    <mesh position={position}>
      <sphereGeometry args={[0.012, 10, 8]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

function CameraRig({ targetY }: { targetY: number }) {
  const { camera, gl, invalidate } = useThree();
  const controlsRef = useRef<OrbitControls | null>(null);

  useEffect(() => {
    const controls = new OrbitControls(camera, gl.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.minDistance = 1.2;
    controls.maxDistance = 15;
    controls.target.set(0, targetY, 0);
    controls.update();
    const onChange = () => invalidate();
    controls.addEventListener("change", onChange);
    controlsRef.current = controls;
    return () => {
      controls.removeEventListener("change", onChange);
      controls.dispose();
      controlsRef.current = null;
    };
  }, [camera, gl, invalidate, targetY]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const controls = controlsRef.current;
      if (!controls || event.defaultPrevented) return;
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "BUTTON" || tag === "SELECT") return;

      const offset = camera.position.clone().sub(controls.target);
      const spherical = new THREE.Spherical().setFromVector3(offset);
      if (event.key === "ArrowLeft") spherical.theta -= 0.08;
      else if (event.key === "ArrowRight") spherical.theta += 0.08;
      else if (event.key === "ArrowUp") spherical.phi = Math.max(0.12, spherical.phi - 0.07);
      else if (event.key === "ArrowDown") spherical.phi = Math.min(Math.PI - 0.12, spherical.phi + 0.07);
      else if (event.key === "+" || event.key === "=") spherical.radius *= 0.9;
      else if (event.key === "-") spherical.radius *= 1.1;
      else return;
      camera.position.copy(controls.target.clone().add(new THREE.Vector3().setFromSpherical(spherical)));
      camera.lookAt(controls.target);
      controls.update();
      invalidate();
      event.preventDefault();
    };
    const canvas = gl.domElement;
    canvas.addEventListener("keydown", onKeyDown);
    return () => canvas.removeEventListener("keydown", onKeyDown);
  }, [camera, gl, invalidate]);

  useFrame(() => controlsRef.current?.update());
  return null;
}

function PartDetails({
  part,
  position,
  selected,
  dimmed,
  onSelect,
}: {
  part: GatewayPart;
  position: readonly [number, number, number];
  selected: boolean;
  dimmed: boolean;
  onSelect: (partId: string) => void;
}) {
  const dimensions = part.approximateDimensionsMm.map((value) => value / 1000) as [
    number,
    number,
    number,
  ];
  const color =
    part.category === "power"
      ? "#323a35"
      : part.category === "network"
        ? "#27342f"
        : part.category === "io"
          ? "#38403b"
          : part.category === "safety"
            ? "#45302f"
            : "#222a27";
  const select = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect(part.id);
  };

  return (
    <group>
      <CadBox
        dimensions={dimensions}
        position={position}
        color={color}
        selected={selected}
        dimmed={dimmed}
        onClick={select}
      />
      {part.id === "fanless-edge-computer" ? (
        <group position={position}>
          {[-0.09, -0.045, 0, 0.045, 0.09].map((x) => (
            <CadBox
              key={x}
              dimensions={[0.012, dimensions[1] * 0.94, dimensions[2] + 0.012]}
              position={[x, 0, 0]}
              color="#111816"
              dimmed={dimmed}
            />
          ))}
          {[-0.075, -0.025, 0.025, 0.075].map((x) => (
            <CadBox
              key={x}
              dimensions={[0.03, 0.022, 0.016]}
              position={[x, -dimensions[1] * 0.24, dimensions[2] / 2 + 0.009]}
              color="#8a978f"
              dimmed={dimmed}
            />
          ))}
          <StatusLed position={[dimensions[0] * 0.36, dimensions[1] * 0.34, dimensions[2] / 2 + 0.014]} />
        </group>
      ) : null}
      {part.id === "industrial-lte-router" ? (
        <group position={position}>
          {[-0.07, 0.07].map((x) => (
            <mesh key={x} position={[x, dimensions[1] / 2 + 0.09, 0]} rotation={[0, 0, x < 0 ? 0.1 : -0.1]}>
              <cylinderGeometry args={[0.009, 0.009, 0.18, 12]} />
              <meshStandardMaterial color="#171d1b" roughness={0.7} />
            </mesh>
          ))}
          <StatusLed position={[0.04, 0.02, dimensions[2] / 2 + 0.012]} />
          <StatusLed position={[0.075, 0.02, dimensions[2] / 2 + 0.012]} color={COLORS.amber} />
        </group>
      ) : null}
      {part.id === "remote-io-module" ? (
        <group position={position}>
          {[-0.06, -0.03, 0, 0.03, 0.06].map((x) => (
            <CadBox
              key={x}
              dimensions={[0.018, 0.025, 0.022]}
              position={[x, dimensions[1] / 2 + 0.01, dimensions[2] / 2]}
              color="#718078"
              dimmed={dimmed}
            />
          ))}
        </group>
      ) : null}
      {part.id === "ethernet-switch" ? (
        <group position={position}>
          {[-0.065, -0.035, -0.005, 0.025, 0.055].map((x) => (
            <CadBox
              key={x}
              dimensions={[0.022, 0.03, 0.018]}
              position={[x, 0, dimensions[2] / 2 + 0.012]}
              color="#86948c"
              dimmed={dimmed}
            />
          ))}
        </group>
      ) : null}
    </group>
  );
}

function GatewayPartVisual({
  part,
  position,
  selected,
  dimmed,
  onSelect,
  onModelError,
  procedural,
}: {
  part: GatewayPart;
  position: readonly [number, number, number];
  selected: boolean;
  dimmed: boolean;
  onSelect: (partId: string) => void;
  onModelError: (partId: string, message: string) => void;
  procedural?: ReactNode;
}) {
  const model = resolvePartModel(part.id);
  if (model.kind === "error") {
    return <ModelErrorMarker part={part} position={position} />;
  }
  if (model.kind === "procedural") {
    return procedural ?? (
      <PartDetails
        part={part}
        position={position}
        selected={selected}
        dimmed={dimmed}
        onSelect={onSelect}
      />
    );
  }
  return (
    <ModelErrorBoundary
      part={part}
      position={position}
      onError={onModelError}
    >
      <Suspense
        fallback={
          <RegisteredModelLoadingMarker part={part} position={position} />
        }
      >
        <RegisteredGatewayPart
          model={model}
          part={part}
          position={position}
          selected={selected}
          dimmed={dimmed}
          onSelect={onSelect}
        />
      </Suspense>
    </ModelErrorBoundary>
  );
}

function ProceduralEnclosure({
  position,
  transparent,
  cutaway,
  selected,
  dimmed,
  onSelect,
}: {
  position: readonly [number, number, number];
  transparent: boolean;
  cutaway: boolean;
  selected: boolean;
  dimmed: boolean;
  onSelect: () => void;
}) {
  const opacity = transparent ? 0.25 : 1;
  const select = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect();
  };
  return (
    <group position={position}>
      <CadBox dimensions={[0.55, 0.75, 0.018]} position={[0, 0, -0.101]} color="#4b5550" opacity={cutaway ? 0.3 : opacity} selected={selected} dimmed={dimmed} onClick={select} />
      {!cutaway ? (
        <>
          <CadBox dimensions={[0.025, 0.8, 0.22]} position={[-0.2875, 0, 0]} color={COLORS.graphite} opacity={opacity} selected={selected} dimmed={dimmed} onClick={select} />
          <CadBox dimensions={[0.025, 0.8, 0.22]} position={[0.2875, 0, 0]} color={COLORS.graphite} opacity={opacity} selected={selected} dimmed={dimmed} onClick={select} />
          <CadBox dimensions={[0.6, 0.025, 0.22]} position={[0, 0.3875, 0]} color={COLORS.graphite} opacity={opacity} selected={selected} dimmed={dimmed} onClick={select} />
          <CadBox dimensions={[0.6, 0.025, 0.22]} position={[0, -0.3875, 0]} color={COLORS.graphite} opacity={opacity} selected={selected} dimmed={dimmed} onClick={select} />
        </>
      ) : null}
    </group>
  );
}

function ProceduralDoor({
  position,
  rotationY,
  transparent,
  selected,
  dimmed,
  onSelect,
}: {
  position: readonly [number, number, number];
  rotationY: number;
  transparent: boolean;
  selected: boolean;
  dimmed: boolean;
  onSelect: () => void;
}) {
  return (
    <group position={position} rotation={[0, rotationY, 0]}>
      <group position={[0.285, 0, 0]}>
        <CadBox
          dimensions={[0.57, 0.77, 0.028]}
          color="#19211e"
          opacity={transparent ? 0.16 : 0.9}
          selected={selected}
          dimmed={dimmed}
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
        />
        <CadBox dimensions={[0.4, 0.012, 0.01]} position={[0, 0.3, 0.02]} color={COLORS.green} dimmed={dimmed} />
      </group>
    </group>
  );
}

function ProceduralLoadCellAssembly({
  position,
  selected,
  dimmed,
  onSelect,
}: {
  position: readonly [number, number, number];
  selected: boolean;
  dimmed: boolean;
  onSelect: () => void;
}) {
  const geometry = LOAD_CELL_ASSEMBLY_GEOMETRY;
  const select = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    onSelect();
  };
  return (
    <group position={position}>
      <mesh position={geometry.hopper.positionMeters} onClick={select}>
        <cylinderGeometry args={[geometry.hopper.radiusTopMeters, geometry.hopper.radiusBottomMeters, geometry.hopper.heightMeters, 4, 1, true]} />
        <meshStandardMaterial color="#56635c" metalness={0.35} roughness={0.58} transparent opacity={dimmed ? 0.18 : 0.72} side={THREE.DoubleSide} />
      </mesh>
      <CadBox dimensions={geometry.plate.dimensionsMeters} position={geometry.plate.positionMeters} color="#6f7b75" selected={selected} dimmed={dimmed} onClick={select} />
      {geometry.loadCellPositionsMeters.map((loadCellPosition) => (
        <CadBox key={loadCellPosition.join("-")} dimensions={geometry.loadCellDimensionsMeters} position={loadCellPosition} color="#95a29b" selected={selected} dimmed={dimmed} onClick={select} />
      ))}
      <CadBox dimensions={geometry.summingJunction.dimensionsMeters} position={geometry.summingJunction.positionMeters} color="#28352f" selected={selected} dimmed={dimmed} onClick={select} />
      <CadBox dimensions={geometry.transmitter.dimensionsMeters} position={geometry.transmitter.positionMeters} color="#1d2d26" selected={selected} dimmed={dimmed} onClick={select} />
      <StatusLed position={[0.145, -0.045, 0.055]} color={COLORS.green} />
      <SegmentLine points={[[0, 0.04, 0.08], [-0.1, -0.02, 0.08], [-0.1, -0.07, 0.04]]} color={COLORS.green} dashed opacity={dimmed ? 0.18 : 0.75} />
      <SegmentLine points={[[-0.04, -0.07, 0.04], [0.015, -0.07, 0.04]]} color={COLORS.blue} opacity={dimmed ? 0.18 : 0.75} />
    </group>
  );
}

function GatewayAssembly({
  parts,
  selectedPartId,
  onSelectPart,
  selectedInterfaceId,
  onSelectInterface,
  explodeAmount,
  doorOpen,
  transparent,
  cutaway,
  showDimensions,
  layers,
  showContext,
  visionNode,
  onModelError,
}: Omit<GatewayCanvasProps, "scene" | "cameraPreset" | "cameraMode" | "pickerCount" | "carrierCount" | "handoffCount" | "sensorCount" | "flowStep" | "updateStep" | "updateFailed" | "reducedMotion" | "paused" | "documentVisible" | "lowQuality"> & {
  showContext: boolean;
}) {
  const selected = selectedPartId !== null || selectedInterfaceId !== null;
  const amount = Math.max(0, Math.min(1, explodeAmount));
  const partPosition = (part: GatewayPart) =>
    part.installedPosition.map(
      (value, index) => value + (part.explodedPosition[index] - value) * amount,
    ) as [number, number, number];
  const visibleParts = parts.filter(
    (part) => !part.optional || !showContext || (part.id === "vision-node" && visionNode),
  );

  return (
    <group>
      {showContext ? (
        <InstalledContext
          layers={layers}
          selectedPartId={selectedPartId}
          selectedInterfaceId={selectedInterfaceId}
          onSelectInterface={onSelectInterface}
        />
      ) : null}
      <group position={showContext ? [-0.95, 0.13, 0] : [0, 0, 0]}>
        {visibleParts.map((part) => {
          const position = partPosition(part);
          const isSelected = selectedPartId === part.id;
          let procedural: ReactNode;
          if (part.id === "gateway-enclosure") {
            procedural = (
              <ProceduralEnclosure
                position={position}
                transparent={transparent}
                cutaway={cutaway}
                selected={isSelected}
                dimmed={selected && !isSelected}
                onSelect={() => onSelectPart(part.id)}
              />
            );
          } else if (part.id === "enclosure-door") {
            const installed = amount < 0.03;
            procedural = (
              <ProceduralDoor
                position={installed ? [-0.3, 0, 0.126] : position}
                rotationY={-(doorOpen ? 1.5 : amount * 0.8)}
                transparent={transparent}
                selected={isSelected}
                dimmed={selected && !isSelected}
                onSelect={() => onSelectPart(part.id)}
              />
            );
          } else if (part.id === "din-rails") {
            procedural = (
              <group position={position}>
                {[-0.15, 0.15].map((y) => (
                  <CadBox key={y} dimensions={[0.48, 0.035, 0.015]} position={[0, y, 0]} color="#89928e" selected={isSelected} dimmed={selected && !isSelected} onClick={(event) => { event.stopPropagation(); onSelectPart(part.id); }} />
                ))}
              </group>
            );
          } else if (part.id === "structured-wiring") {
            procedural = (
              <group position={position}>
                {layers.power ? <SegmentLine points={[[-0.12, -0.3925, 0.07], [-0.265, -0.35, 0.07], [-0.265, -0.12, 0.07], [-0.21, -0.055, 0.07]]} color={COLORS.amber} opacity={selected && !isSelected ? 0.2 : 0.8} /> : null}
                {layers.network ? <SegmentLine points={[[0.1, -0.3925, 0.08], [0.265, -0.35, 0.08], [0.265, 0.31, 0.08], [-0.05, 0.31, 0.08]]} color={COLORS.blue} opacity={selected && !isSelected ? 0.2 : 0.8} /> : null}
                {layers.telemetry ? <SegmentLine points={[[0, -0.3925, 0.09], [0.22, -0.34, 0.09], [0.22, 0.08, 0.09], [-0.12, 0.08, 0.09]]} color={COLORS.green} opacity={selected && !isSelected ? 0.2 : 0.8} dashed /> : null}
                {layers.safety ? <SegmentLine points={[[0.2, -0.3925, 0.1], [0.275, -0.35, 0.1], [0.275, -0.08, 0.1]]} color={COLORS.red} opacity={selected && !isSelected ? 0.2 : 0.9} /> : null}
                {layers.power ? (
                  <group>
                    <SegmentLine points={[[-0.2, -0.3925, 0.11], [-0.25, -0.35, 0.11], [-0.25, 0.31, 0.11]]} color="#7fcb72" opacity={selected && !isSelected ? 0.2 : 0.9} />
                    <SegmentLine points={[[-0.235, -0.1, 0.112], [-0.265, -0.04, 0.112]]} color="#f0d34f" opacity={selected && !isSelected ? 0.2 : 0.9} />
                    <mesh position={[-0.25, 0.31, 0.115]} rotation={[Math.PI / 2, 0, 0]} onClick={(event) => { event.stopPropagation(); onSelectPart(part.id); }}>
                      <torusGeometry args={[0.026, 0.006, 8, 20]} />
                      <meshStandardMaterial color="#b5c46d" emissive={isSelected ? COLORS.green : "#000000"} emissiveIntensity={0.35} />
                    </mesh>
                  </group>
                ) : null}
                <CadBox dimensions={[0.045, 0.045, 0.018]} color="#56635c" selected={isSelected} dimmed={selected && !isSelected} onClick={(event) => { event.stopPropagation(); onSelectPart(part.id); }} />
              </group>
            );
          } else if (part.id === "load-cell-interface") {
            procedural = (
              <ProceduralLoadCellAssembly
                position={position}
                selected={isSelected}
                dimmed={selected && !isSelected}
                onSelect={() => onSelectPart(part.id)}
              />
            );
          }
          return (
            <group key={part.id}>
              {amount > 0.03 ? (
                <SegmentLine
                  points={[part.installedPosition, position]}
                  color={isSelected ? COLORS.green : COLORS.edge}
                  opacity={isSelected ? 0.9 : 0.24}
                  dashed
                />
              ) : null}
              <GatewayPartVisual
                part={part}
                position={position}
                selected={isSelected}
                dimmed={selected && !isSelected}
                onSelect={onSelectPart}
                onModelError={onModelError}
                procedural={procedural}
              />
            </group>
          );
        })}

        {showDimensions ? <DimensionGuide selectedPart={parts.find((part) => part.id === selectedPartId) ?? null} /> : null}
      </group>
    </group>
  );
}

function DimensionGuide({ selectedPart }: { selectedPart: GatewayPart | null }) {
  const width = selectedPart ? selectedPart.approximateDimensionsMm[0] / 1000 : 0.6;
  const y = selectedPart ? selectedPart.installedPosition[1] - 0.18 : -0.48;
  return (
    <group>
      <SegmentLine points={[[-width / 2, y, 0.25], [width / 2, y, 0.25]]} color={COLORS.white} opacity={0.68} />
      <SegmentLine points={[[-width / 2, y - 0.035, 0.25], [-width / 2, y + 0.035, 0.25]]} color={COLORS.white} />
      <SegmentLine points={[[width / 2, y - 0.035, 0.25], [width / 2, y + 0.035, 0.25]]} color={COLORS.white} />
    </group>
  );
}

function InstalledContext({
  layers,
  selectedPartId,
  selectedInterfaceId,
  onSelectInterface,
}: {
  layers: LayerVisibility;
  selectedPartId: string | null;
  selectedInterfaceId: string | null;
  onSelectInterface: (interfaceId: string) => void;
}) {
  const selected = selectedPartId !== null || selectedInterfaceId !== null;
  const renderInterface = (item: InstallationInterface) => {
    const isSelected = selectedInterfaceId === item.id;
    const dimensions = dimensionsMeters(item.approximateDimensionsMm);
    const select = (event: ThreeEvent<MouseEvent>) => {
      event.stopPropagation();
      onSelectInterface(item.id);
    };
    return (
      <group key={item.id}>
        <CadBox
          dimensions={dimensions}
          position={item.installedPosition}
          color={
            item.id === "range-outfield"
              ? "#173324"
              : item.status === "conceptual-connection-point"
                ? "#33423b"
                : "#2d3833"
          }
          opacity={item.id === "range-outfield" ? 0.85 : 1}
          selected={isSelected}
          dimmed={selected && !isSelected}
          onClick={select}
        />
        {item.id === "universal-handoff" ? (
          <mesh position={[0.8, -0.09, 0.46]} onClick={select}>
            <cylinderGeometry args={[0.16, 0.16, 0.16, 18]} />
            <meshStandardMaterial color="#46524d" metalness={0.3} roughness={0.55} />
          </mesh>
        ) : null}
        {item.status === "conceptual-connection-point" ? (
          <StatusLed position={[item.installedPosition[0] + 0.055, item.installedPosition[1] + 0.025, item.installedPosition[2] + 0.035]} color={item.id === "protected-power" ? COLORS.amber : COLORS.blue} />
        ) : null}
      </group>
    );
  };
  return (
    <group>
      <CadBox dimensions={[4.3, 0.08, 2.7]} position={[0, -0.66, 0]} color="#242c29" />
      <CadBox dimensions={[4.3, 2.7, 0.08]} position={[0, 0.68, -0.75]} color="#1b2420" />
      {INSTALLATION_INTERFACES.map(renderInterface)}
      {[0.25, 0.58, 0.91, 1.24, 1.57].map((z) => (
        <SegmentLine key={z} points={[[-0.45, -0.58, z], [0.65, -0.58, z]]} color="#496554" opacity={0.25} />
      ))}
      {layers.power ? <SegmentLine points={[[-1.7, -0.53, -0.63], [-1.7, 0.05, -0.63], [-1.3, 0.05, -0.4]]} color={COLORS.amber} /> : null}
      {layers.network ? <SegmentLine points={[[-1.25, 0.24, -0.4], [0.7, 0.24, -0.4]]} color={COLORS.blue} dashed /> : null}
    </group>
  );
}

function AnimatedPulse({
  start,
  end,
  color,
  offset,
}: {
  start: readonly [number, number, number];
  end: readonly [number, number, number];
  color: string;
  offset: number;
}) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    const progress = (clock.elapsedTime * 0.22 + offset) % 1;
    ref.current?.position.set(
      THREE.MathUtils.lerp(start[0], end[0], progress),
      THREE.MathUtils.lerp(start[1], end[1], progress),
      THREE.MathUtils.lerp(start[2], end[2], progress),
    );
  });
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[0.055, 12, 8]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

function FlowArchitecture({
  step,
  layers,
}: {
  step: number;
  layers: LayerVisibility;
}) {
  const nodes = [
    [-3.8, 0.5, 0],
    [-2.5, 0.5, 0],
    [-1.2, 0.5, 0],
    [0.1, 0.5, 0],
    [1.4, 0.5, 0],
    [2.7, 0.5, 0],
  ] as const;
  return (
    <group>
      <CadBox dimensions={[9.2, 0.06, 3.1]} position={[0, -0.7, 0]} color="#17201c" />
      {nodes.map((position, index) => (
        <CadBox
          key={position[0]}
          dimensions={[0.88, index === 1 ? 1.1 : 0.7, 0.5]}
          position={position}
          color={index === 1 ? "#193628" : index === 5 ? "#29322f" : "#202b27"}
          selected={index <= Math.min(step, 5)}
          dimmed={index > Math.min(step + 1, 5)}
        />
      ))}
      {nodes.slice(0, -1).map((position, index) => {
        const next = nodes[index + 1];
        return layers.telemetry ? (
          <group key={position[0]}>
            <SegmentLine points={[position, next]} color={COLORS.green} opacity={index < step ? 0.9 : 0.25} />
            {index < step ? <AnimatedPulse start={position} end={next} color={COLORS.green} offset={index * 0.17} /> : null}
          </group>
        ) : null;
      })}
      <CadBox dimensions={[2.35, 0.52, 0.42]} position={[0.75, -0.08, 0]} color="#342f22" selected={step >= 4} />
      <SegmentLine points={[[2.7, 0.1, 0], [2.7, -0.25, 0], [3.85, -0.25, 0]]} color={COLORS.edge} opacity={0.4} dashed />
      <CadBox dimensions={[1.25, 0.48, 0.42]} position={[3.85, -0.25, 0]} color="#342927" dimmed />
      <SegmentLine points={[[3.25, -0.25, 0], [3.45, -0.25, 0]]} color={COLORS.red} opacity={0.9} />
      <mesh position={[3.35, -0.25, 0.05]}>
        <octahedronGeometry args={[0.12]} />
        <meshBasicMaterial color={COLORS.red} />
      </mesh>

      <group position={[0, -0.48, 1.05]}>
        <CadBox dimensions={[0.46, 0.16, 0.32]} position={[-1.9, 0, 0]} color="#28342f" />
        <CadBox dimensions={[0.46, 0.16, 0.32]} position={[-1.25, 0, 0]} color="#28342f" />
        <CadBox dimensions={[0.58, 0.2, 0.35]} position={[-0.25, 0, 0]} color="#28342f" />
        <CadBox dimensions={[0.58, 0.25, 0.44]} position={[0.75, 0, 0]} color="#303a36" />
        <CadBox dimensions={[0.72, 0.4, 0.5]} position={[1.85, 0.07, 0]} color="#313b37" />
      </group>
      <SegmentLine points={[[-1.9, -0.48, 1.05], [-2.5, 0.15, 0]]} color={COLORS.green} dashed />
      <SegmentLine points={[[-1.25, -0.48, 1.05], [-2.5, 0.15, 0]]} color={COLORS.green} dashed />
      <SegmentLine points={[[0.75, -0.48, 1.05], [1.85, -0.48, 1.05]]} color={COLORS.edge} opacity={0.32} dashed />
    </group>
  );
}

function FleetArchitecture({
  pickerCount,
  carrierCount,
  handoffCount,
  sensorCount,
  visionNode,
}: Pick<GatewayCanvasProps, "pickerCount" | "carrierCount" | "handoffCount" | "sensorCount" | "visionNode">) {
  const devices = [
    ...Array.from({ length: pickerCount }, (_, index) => ({ kind: "picker", index })),
    ...Array.from({ length: carrierCount }, (_, index) => ({ kind: "carrier", index })),
    ...Array.from({ length: handoffCount }, (_, index) => ({ kind: "handoff", index })),
    ...Array.from({ length: sensorCount }, (_, index) => ({ kind: "sensor", index })),
  ];
  return (
    <group>
      <CadBox dimensions={[0.8, 1.05, 0.36]} position={[-2.2, 0.05, 0]} color="#18251f" selected />
      <CadBox dimensions={[1.6, 0.07, 2.8]} position={[1.3, -0.62, 0]} color="#17201c" />
      {devices.map((device, index) => {
        const row = Math.floor(index / 4);
        const column = index % 4;
        return (
          <group key={`${device.kind}-${device.index}`} position={[0.05 + column * 0.85, -0.34 + row * 0.65, 0]}>
            <CadBox
              dimensions={device.kind === "sensor" ? [0.24, 0.28, 0.24] : [0.52, 0.24, 0.42]}
              color={device.kind === "handoff" ? "#3a403c" : "#26332d"}
            />
            <StatusLed position={[0.15, 0.08, 0.22]} />
            <SegmentLine points={[[-1.85 - column * 0.85, 0.39 - row * 0.65, 0], [-0.35, 0, 0]]} color={COLORS.green} dashed />
          </group>
        );
      })}
      {visionNode ? (
        <group position={[-1.15, -0.33, 0]}>
          <CadBox dimensions={[0.48, 0.35, 0.32]} color="#25342e" selected />
          <StatusLed position={[0.16, 0.09, 0.17]} color={COLORS.blue} />
          <SegmentLine points={[[-0.75, 0.35, 0], [-0.25, 0.35, 0]]} color={COLORS.blue} dashed />
        </group>
      ) : null}
    </group>
  );
}

function UpdateArchitecture({ step, failed }: { step: number; failed: boolean }) {
  const count = 6;
  return (
    <group>
      <CadBox dimensions={[1.2, 0.7, 0.48]} position={[-2.4, 0.45, 0]} color="#24312b" selected={step > 0} />
      <CadBox dimensions={[0.82, 1.05, 0.42]} position={[0, 0.1, 0]} color="#192721" selected={step > 2} />
      <CadBox dimensions={[1.25, 0.64, 0.46]} position={[2.4, 0.45, 0]} color={failed ? "#3b2625" : "#26342d"} selected={step >= 6 && !failed} />
      <SegmentLine points={[[-1.8, 0.45, 0], [-0.45, 0.25, 0]]} color={step > 1 ? COLORS.green : COLORS.edge} dashed={step < 2} />
      <SegmentLine points={[[0.45, 0.25, 0], [1.78, 0.45, 0]]} color={failed ? COLORS.red : step > 4 ? COLORS.green : COLORS.edge} dashed={step < 5} />
      {Array.from({ length: count }, (_, index) => (
        <mesh key={index} position={[-1.45 + index * 0.58, -0.5, 0]}>
          <boxGeometry args={[0.38, 0.08, 0.18]} />
          <meshBasicMaterial color={index < step ? (failed && index >= 4 ? COLORS.red : COLORS.green) : COLORS.graphiteLight} />
        </mesh>
      ))}
      {failed ? (
        <>
          <SegmentLine points={[[2.35, 0.05, 0.3], [1.55, -0.2, 0.3], [0.35, -0.2, 0.3]]} color={COLORS.amber} />
          <CadBox dimensions={[0.65, 0.28, 0.3]} position={[0.95, -0.23, 0.3]} color="#3a3122" selected />
        </>
      ) : null}
    </group>
  );
}

function SafetyArchitecture({ layers }: { layers: LayerVisibility }) {
  return (
    <group>
      <CadBox dimensions={[8.8, 0.06, 3]} position={[0, -0.72, 0]} color="#161e1b" />
      <group position={[0, 0.55, 0]}>
        {[-3.4, -1.7, 0, 1.7, 3.4].map((x, index) => (
          <CadBox key={x} dimensions={[1.05, 0.52, 0.42]} position={[x, 0, 0]} color="#223029" selected={index < 3} />
        ))}
        {layers.telemetry
          ? [-3.4, -1.7, 0, 1.7].map((x) => (
              <SegmentLine key={x} points={[[x + 0.52, 0, 0], [x + 1.18, 0, 0]]} color={COLORS.green} />
            ))
          : null}
      </group>
      {layers.safety ? (
        <group position={[0, -0.28, 0]}>
          {[-2.55, 0, 2.55].map((x, index) => (
            <CadBox key={x} dimensions={[1.25, 0.58, 0.46]} position={[x, 0, 0]} color={index === 0 ? "#4a2727" : "#392526"} selected />
          ))}
          <SegmentLine points={[[-1.92, 0, 0], [-0.63, 0, 0]]} color={COLORS.red} opacity={1} />
          <SegmentLine points={[[0.63, 0, 0], [1.92, 0, 0]]} color={COLORS.red} opacity={1} />
        </group>
      ) : null}
      <SegmentLine points={[[-4.1, 0.1, 0], [4.1, 0.1, 0]]} color={COLORS.edge} opacity={0.16} dashed />
    </group>
  );
}

function GatewayScene(props: GatewayCanvasProps) {
  if (props.scene === "operational-flow") {
    return <FlowArchitecture step={props.flowStep} layers={props.layers} />;
  }
  if (props.scene === "scale-the-fleet") {
    return (
      <FleetArchitecture
        pickerCount={props.pickerCount}
        carrierCount={props.carrierCount}
        handoffCount={props.handoffCount}
        sensorCount={props.sensorCount}
        visionNode={props.visionNode}
      />
    );
  }
  if (props.scene === "software-update") {
    return <UpdateArchitecture step={props.updateStep} failed={props.updateFailed} />;
  }
  if (props.scene === "safety-architecture") {
    return <SafetyArchitecture layers={props.layers} />;
  }
  return (
    <GatewayAssembly
      {...props}
      showContext={props.scene === "installed-system"}
      explodeAmount={props.scene === "exploded-gateway" ? props.explodeAmount : 0}
    />
  );
}

export function GatewayCanvas(props: GatewayCanvasProps) {
  const animatedScene =
    props.scene === "operational-flow" && props.layers.telemetry;
  const animate =
    animatedScene &&
    !props.reducedMotion &&
    !props.paused &&
    props.documentVisible;
  const wideScene =
    props.scene === "operational-flow" || props.scene === "safety-architecture";
  const distance = wideScene
    ? 8.5
    : props.scene === "software-update" || props.scene === "scale-the-fleet"
      ? 6.4
      : 4.7;
  const targetY = wideScene ? 0.1 : 0.05;
  const positions: Record<CameraPreset, [number, number, number]> = {
    installed: [distance * 0.72, distance * 0.45, distance],
    isometric: [distance, distance * 0.74, distance],
    front: [0, 0.12, distance],
    side: [distance, 0.12, 0],
    top: [0.01, distance, 0.01],
  };
  const cameraPosition = positions[props.cameraPreset];

  return (
    <Canvas
      key={`${props.cameraMode}-${props.cameraPreset}-${props.scene}`}
      orthographic={props.cameraMode === "orthographic"}
      camera={
        props.cameraMode === "orthographic"
          ? {
              position: cameraPosition,
              zoom: wideScene ? 62 : 108,
              near: 0.01,
              far: 100,
            }
          : {
              position: cameraPosition,
              fov: 38,
              near: 0.01,
              far: 100,
            }
      }
      dpr={props.lowQuality ? [1, 1.15] : [1, 1.5]}
      frameloop={animate ? "always" : "demand"}
      gl={{ antialias: !props.lowQuality, alpha: false, powerPreference: "high-performance" }}
      shadows={!props.lowQuality}
      onPointerMissed={() => props.onSelectPart("")}
      onCreated={({ gl }) => {
        gl.domElement.tabIndex = 0;
        gl.domElement.setAttribute(
          "aria-label",
          "Interactive conceptual Edge Gateway CAD viewport",
        );
      }}
    >
      <color attach="background" args={["#0a0f0d"]} />
      <fog attach="fog" args={["#0a0f0d", 8, 18]} />
      <ambientLight intensity={1.15} />
      <directionalLight
        position={[4, 7, 5]}
        intensity={2.6}
        color="#e9f4ec"
        castShadow={!props.lowQuality}
      />
      <directionalLight position={[-5, 2, -2]} intensity={1.2} color="#67d895" />
      <gridHelper args={[14, 56, "#3c5d49", "#19271f"]} position={[0, -0.68, 0]} />
      <axesHelper args={[0.42]} position={[-4.1, -0.64, 1.25]} />
      <GatewayScene {...props} />
      <CameraRig targetY={targetY} />
    </Canvas>
  );
}
