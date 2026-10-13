import { useId } from "react";
// A schematic of connected wards, deliberately distinct from clinical data.
export function NetworkIllustration() {
  const pattern = useId();
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 -z-10 overflow-hidden"
    >
      <svg
        viewBox="0 0 560 800"
        preserveAspectRatio="xMaxYMid slice"
        className="h-full w-full opacity-90"
      >
        <defs>
          <pattern
            id={pattern}
            width="32"
            height="32"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r="1" className="fill-white/25" />
          </pattern>
        </defs>
        <rect width="560" height="800" fill={`url(#${pattern})`} />
        <path
          d="M 570 150 H 440 V 320 H 510 V 540 H 390 V 720 H 570 M 440 240 H 350 M 510 410 H 420 M 390 630 H 300"
          fill="none"
          className="stroke-white/40"
          strokeWidth="2"
        />
        {[
          { x: 420, y: 145 },
          { x: 330, y: 230 },
          { x: 490, y: 400 },
          { x: 370, y: 530 },
          { x: 280, y: 620 },
        ].map((ward, index) => (
          <g key={ward.y} transform={`translate(${ward.x} ${ward.y})`}>
            <rect
              x="-12"
              y="-12"
              width="94"
              height="70"
              rx="10"
              className="fill-accent-strong stroke-white/25"
            />
            {Array.from({ length: 6 }, (_, bed) => (
              <rect
                key={bed}
                x={(bed % 3) * 23}
                y={Math.floor(bed / 3) * 25}
                width="15"
                height="17"
                rx="3"
                className={
                  bed === index % 6
                    ? "fill-bed-available"
                    : "fill-white/15 stroke-white/45"
                }
              />
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}
