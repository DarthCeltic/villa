import type { FeedState } from "../api";
import { SystemScreen } from "./SystemScreen";

export function SystemHub({
  feed,
}: {
  feed?: FeedState;
  collection?: string;
  onPickCollection?: (id: string) => void;
}) {
  return <SystemScreen feed={feed} />;
}

export default SystemHub;
