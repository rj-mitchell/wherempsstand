import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Failed, NOT_UPDATED, Pending, serverText } from "../serverText";

function root(html: string): HTMLElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

describe("the server's readable copy of a page", () => {
  const copy = '<h1>Railways Bill</h1><p>Debated in <a href="/debates/x">the Commons</a>.</p>';

  it("is taken from the page's root, but not the placeholder of a page the server did not fill", () => {
    expect(serverText(root(copy))).toBe(copy);
    expect(serverText(root('<p class="muted" role="status">Fetching the bill.</p>'))).toBeNull();
    expect(serverText(null)).toBeNull();
  });

  it("stays in view while the page's data is on its way", () => {
    render(<Pending text={copy}><p>Fetching</p></Pending>);
    expect(screen.getByRole("heading", { level: 1, name: "Railways Bill" })).toBeInTheDocument();
    expect(screen.queryByText("Fetching")).toBeNull();
  });

  it("stays, under a short notice, when the data cannot be fetched (a renderer refused it, say)", () => {
    render(<Failed text={copy}>Failed to fetch</Failed>);
    expect(screen.getByRole("alert")).toHaveTextContent(NOT_UPDATED);
    expect(screen.getByRole("heading", { level: 1, name: "Railways Bill" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "the Commons" })).toHaveAttribute("href", "/debates/x");
    expect(screen.queryByText("Failed to fetch")).toBeNull();
  });

  it("gives way to the page's own message where there is no copy (nothing at the address)", () => {
    render(<Failed text={null}>There is no bill at this address.</Failed>);
    expect(screen.getByRole("alert")).toHaveTextContent("There is no bill at this address.");
    render(<Pending text={null}><p>Fetching the bill.</p></Pending>);
    expect(screen.getByText("Fetching the bill.")).toBeInTheDocument();
  });
});
