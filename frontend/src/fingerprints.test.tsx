import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Fingerprints, Investigation } from "./main";
import { FingerprintsView, findingPath } from "./fingerprints";
import { api } from "./data";
import type { FingerprintsResponse, FindingsResponse, Session } from "./types";
import example from "../public/mock/fingerprints.json";
import session from "../public/mock/s03-workshop-ab-script.session.json";
import findings from "../public/mock/s03-workshop-ab-script.findings.json";

const data = example as FingerprintsResponse;
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
function show(response = data) {
  return render(
    <MemoryRouter>
      <FingerprintsView response={response} />
    </MemoryRouter>,
  );
}

// Controlled UI data only; never added to mocks or the recorded dataset.
function toolCase() {
  const s = structuredClone(session) as Session;
  s.id = "unit-tool-session";
  s.title = "Controlled tool-evidence rendering case";
  s.events = [
    {
      id: "e901",
      t_s: 3,
      type: "tool_execution",
      data: {
        tool_name: "end_call",
        call_ref: "tc001",
        phase: "started",
      },
    },
    {
      id: "e902",
      t_s: 3.1,
      type: "tool_execution",
      data: {
        tool_name: "end_call",
        call_ref: "tc001",
        phase: "ended",
        status: "cancelled",
      },
    },
  ];
  const f = structuredClone((findings as FindingsResponse).findings[0]);
  f.id = `${s.id}:premature_end_call:e901`;
  f.session_id = s.id;
  f.detector = "premature_end_call";
  f.category = "tool_execution";
  f.status = "confirmed";
  f.severity = "high";
  f.title = "end_call dispatched before the recorded closing condition";
  f.summary = "Dispatch does not prove immediate audible disconnection.";
  f.root_cause = {
    confidence: "possible",
    statement: "The agent may have inferred completion.",
    missing_evidence: ["Tool-selection trace"],
  };
  f.turn_ids = [];
  f.time_range = { start_s: 3, end_s: 3 };
  f.evidence = s.events.map((e) => ({
    kind: "event",
    label:
      e.id === "e901"
        ? "end_call dispatched"
        : "Tool terminal status (not disconnection timing)",
    value:
      e.id === "e901"
        ? "call_ref=tc001; phase=started"
        : "call_ref=tc001; phase=ended; status=cancelled",
    event_id: e.id,
    turn_id: null,
    t_s: e.t_s,
  }));
  const o = {
    ...data.fingerprints[0].occurrences[0],
    session_id: s.id,
    session_title: s.title,
    finding_id: f.id,
    title: f.title,
    status: f.status,
    severity: f.severity,
    evidence: f.evidence,
    time_range: f.time_range,
  };
  const response: FingerprintsResponse = {
    ...data,
    fingerprints: [
      {
        ...data.fingerprints[0],
        id: "fp-v1-premature-end-call-dispatch",
        name: "Premature end_call dispatch under the recorded closing policy",
        detector: f.detector,
        category: f.category,
        occurrence_count: 2,
        affected_session_count: 2,
        session_ids: [s.id, "unit-tool-control"],
        status_counts: { confirmed: 2, suspected: 0 },
        severity_counts: { high: 2, medium: 0, low: 0 },
        characteristics: [
          "Dispatch does not prove immediate audible disconnection.",
        ],
        occurrences: [
          o,
          {
            ...o,
            session_id: "unit-tool-control",
            finding_id: "unit-tool-control:finding",
          },
        ],
      },
    ],
  };
  return { s, f, response };
}

describe("failure fingerprints", () => {
  it("renders recorded-only counts, observation rules and both caveats", () => {
    const { container } = show();
    expect(
      screen.getByRole("heading", { name: "Failure Fingerprints" }),
    ).toBeVisible();
    expect(container.querySelectorAll(".fingerprint-card")).toHaveLength(8);
    expect(container.querySelector(".fingerprint-summary")).toHaveTextContent(
      "39 / 42",
    );
    expect(
      screen.getByText(/Some recorded sessions repeat or share scripts/),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Recurring behavior does not prove a shared root cause.",
      ),
    ).toBeVisible();
    const cards = [
      ...container.querySelectorAll<HTMLElement>(".fingerprint-card"),
    ];
    for (const [i, fp] of data.fingerprints.entries()) {
      const card = within(cards[i]);
      expect(card.getByRole("heading", { name: fp.name })).toBeVisible();
      expect(
        card.getByText(
          `${fp.occurrence_count} findings · ${fp.affected_session_count} sessions`,
        ),
      ).toBeVisible();
      expect(
        card.getByText(`${fp.status_counts.confirmed} confirmed`),
      ).toBeVisible();
      expect(
        card.getByText(`${fp.status_counts.suspected} suspected`),
      ).toBeVisible();
      for (const rule of fp.characteristics)
        expect(card.getByText(rule)).toBeVisible();
    }
  });
  it("counts three real premature-dispatch findings without duplicating the regression excerpt", () => {
    const { container } = show();
    const fp = data.fingerprints.find((f) => f.id === "fp-v1-premature-end-call-dispatch")!;
    expect(fp.occurrence_count).toBe(3);
    expect(fp.affected_session_count).toBe(3);
    expect(fp.session_ids).toEqual(["s08-nano-role-confounded-end-call", "s09-recommended-closing-policy", "s11-nano-correction-closing"]);
    expect(fp.occurrences.some((o) => o.session_id === "recorded-nano-premature-end-call")).toBe(false);
    const card = container.querySelector(`#${fp.id}`) as HTMLElement;
    expect(within(card).getByText("3 findings · 3 sessions")).toBeVisible();
    const link = within(card).getAllByRole("link", { name: /end_call dispatched/ })[0];
    expect(link).toHaveAttribute("href", findingPath(fp.occurrences[0].session_id, fp.occurrences[0].finding_id, fp.occurrences[0].evidence[0]));
    expect(within(card).getByText(/observed associations, not causes/)).toBeVisible();
  });
  it("links representative evidence to the exact session, finding and evidence target", () => {
    const { container } = show();
    const o = data.fingerprints[0].occurrences[0];
    const e = o.evidence[0];
    const card = container.querySelector(".fingerprint-card")!;
    expect(within(card as HTMLElement).getAllByRole("link")[0]).toHaveAttribute(
      "href",
      findingPath(o.session_id, o.finding_id, e),
    );
    const url = new URL(
      findingPath(o.session_id, o.finding_id, e),
      "http://localhost",
    );
    expect(url.searchParams.get("finding")).toBe(o.finding_id);
    expect(url.searchParams.get("evidence")).toBe(e.turn_id ?? e.event_id);
  });
  it("exposes original occurrences, context and evidence through native disclosures", () => {
    const { container } = show();
    const card = container.querySelector(".fingerprint-card") as HTMLElement;
    const details = card.querySelector(
      ".fingerprint-details",
    ) as HTMLDetailsElement;
    expect(details.open).toBe(false);
    details.open = true;
    expect(
      within(card)
        .getAllByRole("link")
        .some(
          (link) =>
            link.getAttribute("href") ===
            findingPath(
              data.fingerprints[0].occurrences[0].session_id,
              data.fingerprints[0].occurrences[0].finding_id,
            ),
        ),
    ).toBe(true);
    const context = card.querySelector(
      ".occurrence-context",
    ) as HTMLDetailsElement;
    context.open = true;
    expect(within(context).getByText("Pipeline")).toBeVisible();
    expect(context.querySelectorAll(".fingerprint-evidence li")).toHaveLength(
      data.fingerprints[0].occurrences[0].evidence.length,
    );
  });
  it("accounts for every ungrouped finding and gives its reason without claiming absence of failure", () => {
    const { container } = show();
    const items = [
      ...container.querySelectorAll<HTMLElement>(".ungrouped-list li"),
    ];
    expect(items).toHaveLength(3);
    for (const [i, f] of data.ungrouped.entries()) {
      expect(within(items[i]).getByRole("link")).toHaveAttribute(
        "href",
        findingPath(f.session_id, f.finding_id),
      );
      expect(items[i]).toHaveTextContent(f.explanation);
    }
  });
  it("handles an empty recorded dataset and explicitly excludes synthetic inputs", () => {
    show({
      ...data,
      recorded_session_count: 0,
      finding_count: 0,
      grouped_finding_count: 0,
      fingerprints: [],
      ungrouped: [],
      excluded_synthetic_session_count: 3,
    });
    expect(
      screen.getByText(
        "No recurring patterns meet the evidence and cross-session requirements.",
      ),
    ).toBeVisible();
    expect(screen.getByText(/Excluded 3 synthetic sessions/)).toBeVisible();
  });
  it("shows loading and API failures with a working retry", async () => {
    vi.spyOn(api, "fingerprints")
      .mockRejectedValueOnce(new Error("API unavailable"))
      .mockResolvedValue(data);
    render(
      <MemoryRouter>
        <Fingerprints />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "API unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "Failure Fingerprints" }),
    ).toBeVisible();
  });
  it("opens and highlights the original finding and event from a fingerprint deep link", async () => {
    vi.spyOn(api, "session").mockResolvedValue(session as Session);
    vi.spyOn(api, "findings").mockResolvedValue(findings as FindingsResponse);
    const finding = (findings as FindingsResponse).findings[0];
    const evidence = finding.evidence.find((e) => e.event_id)!;
    render(
      <MemoryRouter
        initialEntries={[findingPath(session.id, finding.id, evidence)]}
      >
        <Routes>
          <Route path="/sessions/:id" element={<Investigation />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: session.title });
    const card = document.getElementById(`finding-${finding.id}`)!;
    expect(card).toHaveClass("highlight");
    expect(
      within(card).getByRole("button", { name: /^Hide investigation/ }),
    ).toHaveAttribute("aria-expanded", "true");
    await waitFor(() =>
      expect(
        document.getElementById(`evidence-${evidence.event_id}`),
      ).toHaveClass("highlight"),
    );
    expect(
      document.getElementById(`evidence-${evidence.event_id}`),
    ).toHaveFocus();
  });
  it("renders tool dispatch and terminal evidence with recurrence caveats", () => {
    const { response } = toolCase();
    const { container } = show(response);
    const card = container.querySelector(".fingerprint-card") as HTMLElement;
    expect(
      within(card).getAllByText("call_ref=tc001; phase=started")[0],
    ).toBeVisible();
    expect(
      within(card).getAllByText(
        "call_ref=tc001; phase=ended; status=cancelled",
      )[0],
    ).toBeVisible();
    expect(within(card).getByText("2 findings · 2 sessions")).toBeVisible();
    expect(
      within(card).getByText(
        "Dispatch does not prove immediate audible disconnection.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Recurring behavior does not prove a shared root cause.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(/Some recorded sessions repeat or share scripts/),
    ).toBeVisible();
    expect(
      within(card).getByText(/observed associations, not causes/),
    ).toBeVisible();
    expect(
      screen.getByText(/not independent trials, failure rates or raw events/),
    ).toBeVisible();
  });
  it("opens and focuses actual tool dispatch evidence from a representative link", async () => {
    const { s, f, response } = toolCase();
    const preview = show(response);
    const card = preview.container.querySelector(
      ".fingerprint-card",
    ) as HTMLElement;
    const href = within(card)
      .getAllByRole("link", { name: /end_call dispatched/ })[0]
      .getAttribute("href")!;
    expect(href).toBe(findingPath(s.id, f.id, f.evidence[0]));
    preview.unmount();
    vi.spyOn(api, "session").mockResolvedValue(s);
    vi.spyOn(api, "findings").mockResolvedValue({
      session_id: s.id,
      detectors_run: ["premature_end_call"],
      findings: [f],
    });
    render(
      <MemoryRouter initialEntries={[href]}>
        <Routes>
          <Route path="/sessions/:id" element={<Investigation />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: s.title });
    const finding = document.getElementById(`finding-${f.id}`)!;
    expect(finding).toHaveClass("highlight");
    expect(
      within(finding).getByRole("button", { name: /^Hide investigation/ }),
    ).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => {
      expect(document.getElementById("evidence-e901")).toHaveClass("highlight");
      expect(document.getElementById("evidence-e901")).toHaveFocus();
    });
    expect(document.getElementById("evidence-e901")).toHaveTextContent(
      /tool name: end_call.*call ref: tc001.*phase: started/i,
    );
    expect(within(finding).getByText("possible")).toBeVisible();
  });
  it("uses the real generated fingerprint fixture in mock mode", async () => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    vi.resetModules();
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(data), { status: 200 }));
    const { api: mockApi } = await import("./data");
    expect(await mockApi.fingerprints()).toEqual(data);
    expect(fetcher).toHaveBeenCalledWith("/mock/fingerprints.json");
  });
});
