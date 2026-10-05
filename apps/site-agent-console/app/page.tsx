"use client";

import { useEffect, useMemo, useState } from "react";
import { ConsoleScreen } from "../components/ConsoleScreen";
import {
  createConsoleController,
  initialConsoleView,
  type ConsoleView,
} from "../lib/actions";
import { createClient } from "../lib/api";
import {
  createConsoleActions,
  readConsole,
  type ConsoleData,
} from "../lib/console";

const client = createClient((input, init) => fetch(input, init));

export default function ConsolePage() {
  const [view, setView] = useState<ConsoleView<ConsoleData>>(() =>
    initialConsoleView<ConsoleData>(),
  );
  // One controller per mounted page. It tags every read with a generation
  // so only the current read may commit data, error, or loading state, and
  // it invalidates everything in flight when the page unmounts.
  const [controller] = useState(() =>
    createConsoleController(() => readConsole(client), setView),
  );

  useEffect(() => {
    controller.start();
    return () => controller.stop();
  }, [controller]);

  const actions = useMemo(
    () => createConsoleActions(client, () => controller),
    [controller],
  );

  return <ConsoleScreen view={view} actions={actions} />;
}
