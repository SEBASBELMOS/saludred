import { expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { imagingApi } from "../../api/imaging";
import { sessionToken } from "../../api/client";
import { renderUi } from "../../test/render";
import { response } from "../../test/fixtures";
import { ImageCanvas } from "./ImageViewer";
it("uploads the original file body without JSON or multipart conversion", async () => {
  sessionToken.set("test-session");
  const fetch = vi.fn().mockResolvedValue(response({ images: 1 }));
  vi.stubGlobal("fetch", fetch);
  const file = new File(["image"], "example.png", { type: "image/png" });
  await imagingApi.upload("study-1", file);
  expect(fetch.mock.calls[0][1].body).toBe(file);
  expect(new Headers(fetch.mock.calls[0][1].headers).get("Content-Type")).toBe(
    "image/png",
  );
  expect(new Headers(fetch.mock.calls[0][1].headers).get("Authorization")).toBe(
    "Bearer test-session",
  );
});
it("loads authenticated previews and revokes object URLs on unmount", async () => {
  sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(new Blob(["image"], { type: "image/png" })),
    );
  vi.stubGlobal("fetch", fetch);
  const create = vi.fn().mockReturnValue("blob:local-preview");
  const revoke = vi.fn();
  URL.createObjectURL = create;
  URL.revokeObjectURL = revoke;
  const { unmount } = renderUi(<ImageCanvas instanceId="instance-1" />);
  await waitFor(() =>
    expect(screen.getByAltText("Vista previa del estudio")).toHaveAttribute(
      "src",
      "blob:local-preview",
    ),
  );
  expect(new Headers(fetch.mock.calls[0][1].headers).get("Authorization")).toBe(
    "Bearer test-session",
  );
  unmount();
  expect(revoke).toHaveBeenCalledWith("blob:local-preview");
});

it("keeps a loading placeholder until the image decodes and releases replaced previews", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(
        async () => new Response(new Blob(["image"], { type: "image/png" })),
      ),
  );
  let sequence = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${++sequence}`);
  URL.revokeObjectURL = vi.fn();
  const { rerender, unmount } = renderUi(<ImageCanvas instanceId="first" />);
  const first = await screen.findByAltText("Vista previa del estudio");
  expect(first).toHaveClass("opacity-0");
  expect(screen.getByText("Cargando imagen…")).toBeInTheDocument();
  fireEvent.load(first);
  expect(first).toHaveClass("opacity-100");
  expect(screen.queryByText("Cargando imagen…")).not.toBeInTheDocument();
  rerender(<ImageCanvas instanceId="second" />);
  await waitFor(() =>
    expect(screen.getByAltText("Vista previa del estudio")).toHaveAttribute(
      "src",
      "blob:preview-2",
    ),
  );
  expect(screen.getByAltText("Vista previa del estudio")).toHaveClass(
    "opacity-0",
  );
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-2");
});
