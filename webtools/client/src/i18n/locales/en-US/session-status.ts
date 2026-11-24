/** Live session state (Redis gateway side) */
export default {
  running: "Running",
  idle: "Idle",
  failed: "Failed",
  stalled: "Stalled",
  unknown: "Unknown",
  runningHint: "Running · run {{run}}",
  lastEvent: "Last event {{event}}",
} as const;
