import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PortalNavigation from "../components/portal-navigation";
import Requests from "../app/requests/page";
import NewRequest from "../app/requests/new/page";
import Approvals from "../app/approvals/page";
import RequestDetails from "../app/requests/[id]/page";

const mockUser = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../lib/portal-user", () => ({ getPortalUser: mockUser.get }));
vi.mock("../components/portal-shell", () => ({
  default: ({ title, children }: { title: string; children: React.ReactNode }) => <main><h1>{title}</h1>{children}</main>,
  PortalAccessNotice: () => <p role="alert">Access restricted</p>,
}));
vi.mock("../components/request-history", () => ({ default: () => <p>History component</p>, RequestHistoryDetail: ({ requestId }: { requestId: string }) => <p>Request {requestId}</p> }));
vi.mock("../components/create-server-form", () => ({ default: () => <p>Request form</p> }));
vi.mock("../components/approval-queue", () => ({ default: () => <p>Approval queue</p> }));

describe("portal screens and navigation", () => {
  it.each([
    { roles: [], names: ["Dashboard"] },
    { roles: ["engineer"], names: ["Dashboard", "New Infrastructure Request", "Requests / History"] },
    { roles: ["approver"], names: ["Dashboard", "Approvals"] },
  ])("shows only role-appropriate navigation for $roles", ({ roles, names }) => {
    render(<PortalNavigation roles={roles} />);
    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(names);
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/");
    if (roles.includes("engineer")) {
      expect(screen.getByRole("link", { name: "Requests / History" })).toHaveAttribute("href", "/requests");
      expect(screen.getByRole("link", { name: "New Infrastructure Request" })).toHaveAttribute("href", "/requests/new");
    }
  });

  it("keeps history separate from the request form", async () => {
    mockUser.get.mockResolvedValue({ id: "engineer", roles: ["engineer"] });
    const view = render(await Requests());
    expect(screen.getByText("History component")).toBeVisible();
    expect(screen.queryByText("Request form")).not.toBeInTheDocument();
    view.unmount(); render(await NewRequest());
    expect(screen.getByText("Request form")).toBeVisible();
    expect(screen.queryByText("History component")).not.toBeInTheDocument();
  });

  it("does not render protected components on unauthenticated direct navigation", async () => {
    mockUser.get.mockResolvedValue(null);
    render(await Requests());
    expect(screen.getByRole("alert")).toHaveTextContent("Access restricted");
    expect(screen.queryByText("History component")).not.toBeInTheDocument();
  });

  it("blocks an engineer from the approvals screen and preserves refreshable detail IDs", async () => {
    mockUser.get.mockResolvedValue({ id: "engineer", roles: ["engineer"] });
    const view = render(await Approvals());
    expect(screen.getByRole("alert")).toBeVisible();
    view.unmount();
    render(await RequestDetails({ params: Promise.resolve({ id: "persisted-id" }) }));
    expect(screen.getByText("Request persisted-id")).toBeVisible();
  });
});
