import Image from "next/image";
import type { CSSProperties } from "react";

import type { YcDemoMissionConfig } from "./yc-dispatch-report.config";
import {
  buildPresentationRoutePath,
  RANGE_SCENE_VIEWBOX,
  scannedRangeScene,
  type NormalizedPoint,
  type SceneLabel,
  type SiteMarker,
} from "./scanned-range-scene.config";
import styles from "./ScannedRangeScene.module.css";

type SceneStyle = CSSProperties & Readonly<Record<`--${string}`, string>>;

type ScannedRangeSceneProps = Readonly<{
  mission: YcDemoMissionConfig;
  onAdvance: () => void;
}>;

function normalizedStyle(point: NormalizedPoint): SceneStyle {
  return {
    "--scene-x": `${point.x * 100}%`,
    "--scene-y": `${point.y * 100}%`,
  };
}

function SceneDescriptor({ label }: { label: SceneLabel }) {
  return (
    <p
      className={`${styles.sceneDescriptor} ${
        label.align === "end" ? styles.alignEnd : styles.alignStart
      }`}
      style={normalizedStyle(label.position)}
    >
      {label.text}
    </p>
  );
}

function Marker({ marker }: { marker: SiteMarker }) {
  return (
    <div className={styles.siteMarker} style={normalizedStyle(marker.position)}>
      <span aria-hidden="true" />
      <strong>{marker.label}</strong>
    </div>
  );
}

export function ScannedRangeScene({
  mission,
  onAdvance,
}: ScannedRangeSceneProps) {
  const routePath = buildPresentationRoutePath();
  const robotStart = {
    x: scannedRangeScene.robotStart.position.x * RANGE_SCENE_VIEWBOX.width,
    y: scannedRangeScene.robotStart.position.y * RANGE_SCENE_VIEWBOX.height,
  };
  const sceneStyle = {
    "--scene-object-x": `${scannedRangeScene.objectPosition.x * 100}%`,
    "--scene-object-y": `${scannedRangeScene.objectPosition.y * 100}%`,
  } as SceneStyle;

  return (
    <div
      className={styles.scene}
      data-animation-kind="presentation-only-route"
      data-scene-kind="site-presentation-schematic"
      style={sceneStyle}
    >
      <Image
        alt={scannedRangeScene.backgroundAlt}
        className={styles.background}
        fill
        preload
        sizes="(max-width: 1024px) calc(100vw - 56px), 90vw"
        src={scannedRangeScene.backgroundImage}
      />

      <div aria-hidden="true" className={styles.scanTexture} />

      <h1 className={styles.missionHeading} id="dispatch-status">
        <button
          aria-describedby="operator-shortcuts"
          aria-label="Mission dispatched — show mission report"
          className={styles.missionTrigger}
          onClick={onAdvance}
          type="button"
        >
          <span className={styles.missionKicker}>01 / Mission state</span>
          <span className={styles.missionTitle}>
            Mission <strong>Dispatched</strong>
          </span>
        </button>
      </h1>

      <div className={styles.statusBadge}>
        <i aria-hidden="true" />
        Dispatched
      </div>

      <SceneDescriptor label={scannedRangeScene.facilityLabel} />
      <SceneDescriptor label={scannedRangeScene.sceneLabel} />
      <SceneDescriptor label={scannedRangeScene.mapLabel} />

      <svg
        aria-label="Presentation-only route animation across the site presentation schematic"
        className={styles.routeOverlay}
        role="img"
        viewBox={`0 0 ${RANGE_SCENE_VIEWBOX.width} ${RANGE_SCENE_VIEWBOX.height}`}
      >
        <path className={styles.routeHalo} d={routePath} />
        <path className={styles.routeLine} d={routePath} />
        <g className={styles.animatedRobot} data-robot-marker={mission.robotName}>
          <circle className={styles.robotRing} r="10" />
          <circle className={styles.robotCore} r="4" />
          <animateMotion
            calcMode="spline"
            dur={`${scannedRangeScene.animationDurationMs}ms`}
            keySplines="0.45 0 0.25 1"
            path={routePath}
            repeatCount="indefinite"
          />
        </g>
        <g
          className={styles.staticRobot}
          data-robot-marker={`${mission.robotName}-reduced-motion`}
          transform={`translate(${robotStart.x} ${robotStart.y})`}
        >
          <circle className={styles.robotRing} r="10" />
          <circle className={styles.robotCore} r="4" />
        </g>
      </svg>

      <Marker marker={scannedRangeScene.teeLine} />
      <Marker marker={scannedRangeScene.zoneA} />
      <Marker marker={scannedRangeScene.returnStation} />
      <Marker marker={scannedRangeScene.robotStart} />

      <p className={styles.animationDisclosure}>
        Presentation-only route animation
      </p>

      <dl className={styles.missionRail}>
        <div>
          <dt>Robot</dt>
          <dd>{mission.robotName}</dd>
        </div>
        <div>
          <dt>Task</dt>
          <dd>{mission.taskName}</dd>
        </div>
        <div>
          <dt>Zone</dt>
          <dd>{mission.zoneName}</dd>
        </div>
        <div>
          <dt>Mission ID</dt>
          <dd>{mission.missionId}</dd>
        </div>
        <div className={styles.railStatus}>
          <dt>Status</dt>
          <dd>Dispatched</dd>
        </div>
      </dl>
    </div>
  );
}
