import { useEffect, useRef, useState } from "react";
import { sessionToken } from "../../api/client";
import type { AppNotification } from "../../api/types";

/**
 * connecting: first attempt. live: socket open. reconnecting: it dropped and
 * is being retried. polling: the socket could not connect after a few retries,
 * so the unread inbox is read on a timer (graceful degradation, not the main path).
 */
export type LiveState = "connecting" | "live" | "reconnecting" | "polling";

export const POLL_MS = 20_000;
export const RETRIES_BEFORE_POLLING = 3;
// The API pings every 15 s: three missed pings mean the socket is half-open.
export const SILENCE_MS = 45_000;
const MAX_BACKOFF_MS = 15_000;
// Close code the API uses for a refused token: retrying with it is pointless.
export const WS_UNAUTHORIZED = 4401;

export const backoffMs = (failures: number) =>
  Math.min(1000 * 2 ** Math.max(0, failures - 1), MAX_BACKOFF_MS);

// Same origin as the page: wss behind https (the tunnel), ws on the LAN.
export function liveUrl(token: string, location: Location = window.location) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/api/v1/events/ws?token=${encodeURIComponent(token)}`;
}

interface Options {
  enabled: boolean;
  onEvent: (event: AppNotification) => void;
  // Called after every (re)connection, to catch up with what happened offline.
  onSync: () => void;
  // Called on each tick of the polling fallback.
  onPoll: () => void;
}

export function useLiveEvents({
  enabled,
  onEvent,
  onSync,
  onPoll,
}: Options): LiveState {
  const [state, setState] = useState<LiveState>("connecting");
  const handlers = useRef({ onEvent, onSync, onPoll });
  handlers.current = { onEvent, onSync, onPoll };

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let silenceTimer: ReturnType<typeof setTimeout> | undefined;
    let pollTimer: ReturnType<typeof setInterval> | undefined;
    let failures = 0;
    let stopped = false;

    const startPolling = () => {
      setState("polling");
      if (pollTimer) return;
      handlers.current.onPoll();
      pollTimer = setInterval(() => handlers.current.onPoll(), POLL_MS);
    };
    const stopPolling = () => {
      clearInterval(pollTimer);
      pollTimer = undefined;
    };
    const armSilence = () => {
      clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => socket?.close(), SILENCE_MS);
    };

    const connect = () => {
      const token = sessionToken.get();
      if (stopped || !token || typeof WebSocket === "undefined") {
        if (!stopped) startPolling();
        return;
      }
      const current = new WebSocket(liveUrl(token));
      socket = current;
      current.onopen = () => {
        failures = 0;
        stopPolling();
        setState("live");
        armSilence();
        handlers.current.onSync();
      };
      current.onmessage = (message) => {
        armSilence();
        let data: unknown;
        try {
          data = JSON.parse(String(message.data));
        } catch {
          return;
        }
        if (!data || typeof data !== "object") return;
        const event = data as Partial<AppNotification>;
        if (event.tipo === "ping" || !event.tipo) return;
        handlers.current.onEvent(event as AppNotification);
      };
      current.onclose = (close) => {
        clearTimeout(silenceTimer);
        if (socket === current) socket = null;
        if (stopped) return;
        if (close.code === WS_UNAUTHORIZED) {
          // The polling request will surface the 401 and end the session.
          startPolling();
          return;
        }
        failures += 1;
        if (failures >= RETRIES_BEFORE_POLLING) startPolling();
        else setState("reconnecting");
        retryTimer = setTimeout(connect, backoffMs(failures));
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearTimeout(silenceTimer);
      stopPolling();
      if (socket) {
        socket.onclose = null;
        socket.close(1000);
      }
    };
  }, [enabled]);

  return state;
}
