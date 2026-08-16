import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnalystWorkspace } from "@/components/analyst-workspace";

describe("AnalystWorkspace", () => {
  it("presents the evidence and human-review trust boundary", () => {
    render(<AnalystWorkspace />);

    expect(
      screen.getByRole("heading", { name: /investigate filings/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/public sec data only/i)).toBeInTheDocument();
    expect(screen.getByText(/answers remain blocked/i)).toBeInTheDocument();
  });

  it("loads a clearly labelled interface fixture with provenance", () => {
    render(<AnalystWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: /explore interface fixture/i }));

    expect(screen.getByRole("status")).toHaveTextContent(/not a live model result/i);
    expect(screen.getByRole("heading", { name: /evidence-backed report/i })).toBeInTheDocument();
    expect(screen.getByText(/apple inc. 2025 form 10-k/i)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /human release gate/i }),
    ).toBeInTheDocument();
  });

  it("requires an attributable reviewer before a fixture decision", () => {
    render(<AnalystWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: /explore interface fixture/i }));
    fireEvent.click(screen.getByRole("button", { name: /approve release/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/reviewer identifier/i);
  });

  it("records the fixture review and feedback without claiming a backend write", () => {
    render(<AnalystWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: /explore interface fixture/i }));
    fireEvent.change(screen.getByLabelText(/reviewer identifier/i), {
      target: { value: "analyst@example.org" },
    });
    fireEvent.click(screen.getByRole("button", { name: /approve release/i }));

    expect(screen.getByText(/review recorded: approved/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /submit feedback/i }));
    expect(screen.getByText(/feedback recorded/i)).toBeInTheDocument();
  });
});
