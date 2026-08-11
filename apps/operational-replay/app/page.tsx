import type { Metadata } from "next";
import { ReplayStory } from "./ReplayStory";

export const metadata: Metadata = {
  title: "NXTektal Replay Story",
  description:
    "A read-only storytelling layer for NXTektal operational replay artifacts.",
};

export default function Home() {
  return <ReplayStory />;
}
