import { screen } from "@testing-library/react";
import { expect, it } from "vitest";
import {
  Action,
  buttonFocus,
  buttonLooks,
  interaction,
  linkButton,
} from "./ui";
import { renderUi } from "../test/render";

// The professor asked for buttons that react to the pointer (15/09). Every variant
// changes colour on hover, shows a focus ring and presses to 0.97, all in 160 ms.
const variants: [string, { secondary?: boolean; danger?: boolean }][] = [
  ["primary", {}],
  ["secondary", { secondary: true }],
  ["danger", { danger: true }],
  ["danger secondary", { danger: true, secondary: true }],
];

it.each(variants)("the %s button reacts to hover, focus and press", (name, props) => {
  renderUi(<Action label={name} {...props} />);
  const button = screen.getByRole("button", { name });
  const classes = button.className.split(/\s+/);
  // Colour and border change, only where a real pointer exists.
  expect(classes.some((c) => c.startsWith("hov:bg-"))).toBe(true);
  expect(classes.some((c) => c.startsWith("hov:border-"))).toBe(true);
  expect(classes.some((c) => /^hover:/.test(c))).toBe(false);
  // 160 ms with the project easing, never `transition: all`.
  expect(classes).toContain("duration-press");
  expect(classes).toContain("ease-out");
  expect(classes).not.toContain("transition-all");
  expect(
    classes.find((c) => c.startsWith("transition-[")),
  ).toContain("background-color");
  // The press and the focus ring.
  expect(classes).toContain("motion-safe:enabled:active:scale-[0.97]");
  expect(classes).toContain("focus-visible:outline-accent");
  expect(classes).toContain("focus-visible:outline-offset-2");
});
it("keeps the hover colour apart from the resting colour in every variant", () => {
  for (const look of Object.values(buttonLooks)) {
    const resting = look.split(" ").find((c) => c.startsWith("bg-"))!;
    const hover = look.split(" ").find((c) => c.startsWith("hov:bg-"))!;
    expect(hover.replace("hov:", "")).not.toBe(resting);
  }
});
it("animates the focus ring with the same transition as the hover", () => {
  expect(interaction).toContain("outline-color");
  expect(interaction).toContain("outline-offset");
  expect(buttonFocus).toContain("outline-transparent");
});
it("fades the colour of text buttons and never lifts or enlarges them", () => {
  expect(linkButton).toContain("hov:text-accent-strong");
  expect(linkButton).toContain("transition-colors");
  for (const text of [interaction, linkButton, ...Object.values(buttonLooks)])
    expect(text).not.toMatch(/hov:(-translate|translate|scale|shadow)/);
});
