import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { api, compareMock, time, winner } from "./data";
import { EvidenceLink, Timeline, Investigation } from "./main";
import type { FindingsResponse, Session } from "./types";
import a from "../public/mock/s03-workshop-ab-script.session.json";
import b from "../public/mock/s04-recommended-ab-script.session.json";
import fa from "../public/mock/s03-workshop-ab-script.findings.json";
import fb from "../public/mock/s04-recommended-ab-script.findings.json";
import expected from "../../contracts/examples/compare.json";
const sessions = import.meta.glob<Session>("../public/mock/*.session.json", {
  eager: true,
  import: "default",
});
const findings = import.meta.glob<FindingsResponse>(
  "../public/mock/*.findings.json",
  { eager: true, import: "default" },
);
describe("time formatting", () => {
  it.each([
    [null, "n/a"],
    [0, "0:00.0"],
    [65.42, "1:05.4"],
    [59.99, "1:00.0"],
    [416.929, "6:56.9"],
  ] as const)("formats %s", (value, label) => expect(time(value)).toBe(label));
});
describe("real detector fixtures", () => {
  it("loads seven complete sessions and 34 real findings", () => {
    expect(Object.keys(sessions)).toHaveLength(7);
    expect(Object.keys(findings)).toHaveLength(7);
    const all = Object.values(findings).flatMap((r) => r.findings);
    expect(all).toHaveLength(34);
    expect(all.filter((f) => f.status === "confirmed")).toHaveLength(22);
    expect(all.filter((f) => f.status === "suspected")).toHaveLength(12);
  });
  it("resolves every evidence reference inside its own session", () => {
    for (const response of Object.values(findings)) {
      const session = Object.values(sessions).find(
        (s) => s.id === response.session_id,
      )!;
      expect(session).toBeDefined();
      for (const f of response.findings)
        for (const e of f.evidence) {
          if (e.turn_id)
            expect(session.turns.some((t) => t.id === e.turn_id)).toBe(true);
          if (e.event_id)
            expect(session.events.some((t) => t.id === e.event_id)).toBe(true);
        }
    }
  });
});
describe("comparison semantics", () => {
  it("matches the actual backend example exactly", () =>
    expect(
      compareMock(a, b, fa as FindingsResponse, fb as FindingsResponse),
    ).toEqual(expected));
  it("does not rank ties, nulls or metrics without a direction", () => {
    const m = expected.metrics[0];
    expect(winner({ ...m, unit: "s", better: null })).toBeNull();
    expect(winner({ ...m, unit: "s", better: "lower", a: null })).toBeNull();
    expect(winner({ ...m, unit: "s", better: "lower", a: 1, b: 1 })).toBeNull();
    expect(winner({ ...m, unit: "s", better: "higher", a: 1, b: 2 })).toBe("b");
  });
});
describe("evidence navigation", () => {
  it.each(["turn", "event"])("navigates to its linked %s", (kind) => {
    const navigate = vi.fn();
    const e = {
      kind: "transcript" as const,
      label: "Observed text",
      value: "Source",
      turn_id: kind === "turn" ? "t001" : null,
      event_id: kind === "event" ? "e001" : null,
      t_s: 12,
    };
    render(<EvidenceLink e={e} onNavigate={navigate} />);
    fireEvent.click(screen.getByRole("button"));
    expect(navigate).toHaveBeenCalledWith(kind === "turn" ? "t001" : "e001");
  });
  it("does not invent a destination for unlinked evidence", () => {
    render(
      <EvidenceLink
        e={{
          kind: "config",
          label: "Configuration",
          value: "enabled",
          turn_id: null,
          event_id: null,
          t_s: null,
        }}
        onNavigate={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
describe("timeline rendering", () => {
  it("renders actual events and highlights the evidence target", () => {
    const events = (a as Session).events;
    const target = events.find((e) => e.type === "false_interruption")!;
    const { container } = render(
      <Timeline events={events} active={target.id} />,
    );
    expect(container.querySelectorAll(".event")).toHaveLength(events.length);
    expect(document.getElementById(`evidence-${target.id}`)).toHaveClass(
      "highlight",
    );
    expect(screen.getAllByText("false interruption").length).toBeGreaterThan(0);
  });
  it("handles an empty timeline", () => {
    render(<Timeline events={[]} active={null} />);
    expect(screen.getByText("No recorded events.")).toBeInTheDocument();
  });
});

describe("session investigation integration", () => {
  it("switches to events, focuses evidence, and switches back to the transcript", async () => {
    vi.spyOn(api, "session").mockResolvedValue(a as Session);
    vi.spyOn(api, "findings").mockResolvedValue(fa as FindingsResponse);
    render(
      <MemoryRouter initialEntries={["/sessions/" + a.id]}>
        <Routes>
          <Route path="/sessions/:id" element={<Investigation />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText(fa.findings[0].title);
    fireEvent.click(
      screen.getAllByRole("button", { name: /^View investigation/ })[0],
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: /User voice activity started/ })[0],
    );
    const event = fa.findings[0].evidence.find((e) => e.event_id)!;
    expect(document.getElementById("evidence-" + event.event_id)).toHaveClass(
      "highlight",
    );
    expect(document.activeElement).toBe(
      document.getElementById("evidence-" + event.event_id),
    );
    expect(screen.getByRole("tab", { name: /timeline/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: /Agent reply affected/ })[0],
    );
    const turn = fa.findings[0].evidence.find((e) => e.turn_id)!;
    expect(document.getElementById("evidence-" + turn.turn_id)).toHaveClass(
      "highlight",
    );
    expect(screen.getByRole("tab", { name: /transcript/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    vi.restoreAllMocks();
  });
});

describe("investigation progressive disclosure", () => {
  afterEach(() => vi.restoreAllMocks());
  async function openSession() {
    vi.spyOn(api, "session").mockResolvedValue(a as Session);
    vi.spyOn(api, "findings").mockResolvedValue(fa as FindingsResponse);
    const view = render(
      <MemoryRouter initialEntries={["/sessions/" + a.id]}>
        <Routes>
          <Route path="/sessions/:id" element={<Investigation />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: a.title });
    return view;
  }
  it("starts findings collapsed while preserving status, severity and diagnostic metadata", async () => {
    const { container } = await openSession();
    const cards = [...container.querySelectorAll<HTMLElement>(".finding")];
    expect(cards).toHaveLength(fa.findings.length);
    for (const [i, finding] of fa.findings.entries()) {
      const card = within(cards[i]);
      const toggle = card.getByRole("button", {
        name: `View investigation ${finding.title}`,
      });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(
        document.getElementById(toggle.getAttribute("aria-controls")!),
      ).not.toBeVisible();
      expect(card.getByText(finding.summary)).toBeVisible();
      expect(card.getByText(finding.status)).toBeVisible();
      expect(card.getByText(`${finding.severity} severity`)).toBeVisible();
      expect(
        card.getByText(`${finding.evidence.length} evidence items`),
      ).toBeVisible();
      expect(cards[i]).toHaveTextContent(
        `Root-cause confidence · ${finding.root_cause.confidence}`,
      );
      expect(
        card.queryByRole("heading", { name: "Observed evidence" }),
      ).not.toBeInTheDocument();
    }
  });
  it("uses a focusable native button and reveals the unchanged diagnostic content on activation", async () => {
    await openSession();
    const f = fa.findings[0];
    const toggle = screen.getByRole("button", {
      name: `View investigation ${f.title}`,
    });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.tabIndex).toBe(0);
    toggle.focus();
    expect(toggle).toHaveFocus();
    // Keyboard activation of native buttons dispatches a click with detail 0.
    fireEvent.click(toggle, { detail: 0 });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = within(
      document.getElementById(toggle.getAttribute("aria-controls")!)!,
    );
    expect(
      panel.getByRole("heading", { name: "Observed evidence" }),
    ).toBeVisible();
    expect(panel.getByText(f.root_cause.statement)).toBeVisible();
    for (const e of f.evidence) expect(panel.getByText(e.value)).toBeVisible();
    for (const missing of f.root_cause.missing_evidence)
      expect(panel.getByText(missing)).toBeVisible();
    for (const step of f.next_steps)
      expect(panel.getByText(step)).toBeVisible();
    fireEvent.click(toggle, { detail: 0 });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(panel.getByText(f.root_cause.statement)).not.toBeVisible();
    expect(toggle).toHaveFocus();
  });
  it("expands findings independently", async () => {
    await openSession();
    const toggles = screen.getAllByRole("button", {
      name: /^View investigation/,
    });
    fireEvent.click(toggles[0]);
    expect(toggles[0]).toHaveAttribute("aria-expanded", "true");
    expect(toggles[1]).toHaveAttribute("aria-expanded", "false");
  });
  it("discloses the exact detector names and actual coverage count", async () => {
    await openSession();
    const toggle = screen.getByRole("button", {
      name: `Detection coverage · ${fa.detectors_run.length} detectors ran`,
    });
    const panel = document.getElementById(
      toggle.getAttribute("aria-controls")!,
    )!;
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(panel).not.toBeVisible();
    toggle.focus();
    fireEvent.click(toggle, { detail: 0 });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const names = within(panel).getAllByRole("listitem");
    expect(names.map((item) => item.textContent)).toEqual(fa.detectors_run);
    expect(panel).toBeVisible();
    fireEvent.click(toggle);
    expect(panel).not.toBeVisible();
  });
});
