import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, compareMock, mockMode, seconds, winner } from "./data";
import { Compare, Dashboard, Investigation } from "./main";

// Opt-in tests use actual HTTP responses, never mocked fetches or fixture data.
const base = (import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
const ids = [
  "s01-workshop-improvised",
  "s02-workshop-structured",
  "s03-workshop-ab-script",
  "s04-recommended-ab-script",
  "s05-workshop-headphones",
  "s06-hybrid-script",
  "s07-workshop-persona-v2",
];

describe.skipIf(import.meta.env.VITE_RUN_LIVE_TESTS !== "true")(
  "live FastAPI integration",
  () => {
    it("loads the dashboard's seven sessions and all 34 findings", async () => {
      expect(mockMode).toBe(false);
      const summaries = await api.sessions();
      expect(summaries.map((s) => s.id)).toEqual(ids);
      const findings = (
        await Promise.all(ids.map((id) => api.findings(id)))
      ).flatMap((r) => r.findings);
      expect(findings).toHaveLength(34);
      expect(findings.filter((f) => f.status === "confirmed")).toHaveLength(22);
      expect(findings.filter((f) => f.status === "suspected")).toHaveLength(12);
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>,
      );
      await screen.findByRole("heading", { name: /Session explorer/ });
      for (const s of summaries)
        expect(
          screen.getByRole("link", {
            name: new RegExp(s.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
          }),
        ).toHaveAttribute("href", `/sessions/${s.id}`);
      fireEvent.click(screen.getAllByRole("checkbox")[2]);
      fireEvent.click(screen.getAllByRole("checkbox")[3]);
      expect(
        screen.getByRole("link", { name: "Compare sessions →" }),
      ).toHaveAttribute("href", `/compare?a=${ids[2]}&b=${ids[3]}`);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it.each(ids)(
      "renders every finding and follows every evidence link for %s",
      async (id) => {
        const [session, response] = await Promise.all([
          api.session(id),
          api.findings(id),
        ]);
        const { container } = render(
          <MemoryRouter initialEntries={[`/sessions/${id}`]}>
            <Routes>
              <Route path="/sessions/:id" element={<Investigation />} />
            </Routes>
          </MemoryRouter>,
        );
        await screen.findByRole("heading", { name: session.title });
        const cards = [...container.querySelectorAll<HTMLElement>(".finding")];
        expect(cards).toHaveLength(response.findings.length);
        for (const [index, finding] of response.findings.entries()) {
          const card = cards[index];
          expect(
            within(card).getByRole("heading", { name: finding.title }),
          ).toBeInTheDocument();
          const toggle = within(card).getByRole("button", {
            name: /^View investigation/,
          });
          expect(toggle).toHaveAttribute("aria-expanded", "false");
          fireEvent.click(toggle);
          expect(toggle).toHaveAttribute("aria-expanded", "true");
          expect(
            within(card).getByText(finding.root_cause.statement),
          ).toBeVisible();
          const linked = finding.evidence.filter(
            (e) => e.turn_id || e.event_id,
          );
          const details = document.getElementById(
            toggle.getAttribute("aria-controls")!,
          )!;
          const buttons = within(details).queryAllByRole("button");
          expect(buttons).toHaveLength(linked.length);
          for (const [i, e] of linked.entries()) {
            if (e.turn_id)
              expect(session.turns.some((t) => t.id === e.turn_id)).toBe(true);
            if (e.event_id)
              expect(session.events.some((t) => t.id === e.event_id)).toBe(
                true,
              );
            fireEvent.click(buttons[i]);
            const target = document.getElementById(
              `evidence-${e.turn_id ?? e.event_id}`,
            );
            expect(target).toHaveClass("highlight");
            expect(target).toHaveFocus();
          }
        }
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      },
      30000,
    );

    it("matches backend comparisons for all 21 distinct pairs", async () => {
      const sessions = await Promise.all(ids.map((id) => api.session(id)));
      const findings = await Promise.all(ids.map((id) => api.findings(id)));
      for (let a = 0; a < ids.length; a++)
        for (let b = a + 1; b < ids.length; b++) {
          expect(await api.compare(ids[a], ids[b])).toEqual(
            compareMock(sessions[a], sessions[b], findings[a], findings[b]),
          );
        }
    }, 30000);

    it("renders the actual comparison metrics, caveats, and supported highlights", async () => {
      const c = await api.compare(ids[2], ids[3]);
      const { container } = render(
        <MemoryRouter initialEntries={[`/compare?a=${ids[2]}&b=${ids[3]}`]}>
          <Compare />
        </MemoryRouter>,
      );
      await screen.findByRole("heading", { name: "Compare the evidence." });
      for (const caveat of c.caveats)
        expect(screen.getByText(caveat)).toBeInTheDocument();
      for (const m of c.metrics) {
        const row = screen
          .getByRole("rowheader", { name: m.label })
          .closest("tr")!;
        const cells = within(row).getAllByRole("cell");
        for (const [index, side] of (["a", "b"] as const).entries()) {
          expect(cells[index]).toHaveTextContent(
            m.unit === "s" ? seconds(m[side]) : String(m[side] ?? "n/a"),
          );
          expect(cells[index].classList.contains("better")).toBe(
            winner(m) === side,
          );
        }
      }
      expect(container.querySelectorAll(".compare-head a")).toHaveLength(2);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("permits both Vite origins, GETs and preflight requests through CORS", async () => {
      for (const origin of ["http://localhost:5173", "http://127.0.0.1:5173"]) {
        for (const path of [
          "/api/sessions",
          `/api/sessions/${ids[2]}`,
          `/api/sessions/${ids[2]}/findings`,
          `/api/compare?a=${ids[2]}&b=${ids[3]}`,
        ]) {
          const response = await fetch(base + path, {
            headers: { Origin: origin },
          });
          expect(response.status).toBe(200);
          expect(response.headers.get("access-control-allow-origin")).toBe(
            origin,
          );
        }
        const preflight = await fetch(base + "/api/sessions", {
          method: "OPTIONS",
          headers: {
            Origin: origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
          },
        });
        expect(preflight.status).toBe(200);
        expect(preflight.headers.get("access-control-allow-origin")).toBe(
          origin,
        );
      }
    });

    it("serves all three Vite routes in live mode and displays API 404 errors", async () => {
      for (const path of [
        "/",
        `/sessions/${ids[2]}`,
        `/compare?a=${ids[2]}&b=${ids[3]}`,
      ]) {
        const response = await fetch("http://localhost:5173" + path);
        expect(response.status).toBe(200);
        expect(await response.text()).toContain("/src/main.tsx");
      }
      const env = await fetch("http://localhost:5173/src/data.ts").then((r) =>
        r.text(),
      );
      expect(env).toContain('"VITE_USE_MOCKS": "false"');
      render(
        <MemoryRouter initialEntries={["/sessions/missing-session"]}>
          <Routes>
            <Route path="/sessions/:id" element={<Investigation />} />
          </Routes>
        </MemoryRouter>,
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Session not found.",
      );
    });
  },
);
