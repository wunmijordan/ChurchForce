(function () {
  "use strict";

  /** Tabler Icons–style outline SVGs: uniform 20×20, 24 viewBox, stroke caps. */
  function tb(stroke, paths) {
    const inner = paths.map((d) => `<path d="${d}" />`).join("");
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" ` +
      `stroke="${stroke}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">` +
      `<path stroke="none" d="M0 0h24v24H0z" fill="none"/>` +
      inner +
      `</svg>`
    );
  }

  const SVG = {
    like: tb("currentColor", [
      "M7 11v8a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1v-7a1 1 0 0 1 1 -1h3a4 4 0 0 0 4 -4v-1a2 2 0 0 1 4 0v5h3a2 2 0 0 1 2 2l-1 5a2 3 0 0 1 -2 2h-7a3 3 0 0 1 -3 -3",
    ]),
    love: tb("#ef4444", [
      "M19.5 12.572l-7.5 7.428l-7.5 -7.428a5 5 0 1 1 7.5 -6.566a5 5 0 1 1 7.5 6.572",
    ]),
    fire: tb("#f97316", [
      "M12 10.941c2.333 -3.308 .167 -7.823 -1 -8.941c0 3.395 -2.235 5.299 -3.667 6.706c-1.43 1.408 -2.333 3.294 -2.333 5.588c0 3.704 3.134 6.706 7 6.706c3.866 0 7 -3.002 7 -6.706c0 -1.712 -1.232 -4.403 -2.333 -5.588c-2.084 3.353 -3.257 3.353 -4.667 2.235",
    ]),
    pray: tb("#a78bfa", [
      "M12 5m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0",
      "M7 20h8l-4 -4v-7l4 3l2 -2",
    ]),
    clap: tb("#fbbf24", [
      "M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z",
    ]),
    amen: tb("#34d399", [
      "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0",
      "M9 12l2 2l4 -4",
    ]),
    bless: tb("#fde68a", [
      "M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z",
    ]),
    inspired: tb("#fa0404", [
      "M2 8h4",
      "M4 8v8",
      "M13 8h-4v8h4",
      "M9 12h2.5",
      "M16 8v8h2a3 3 0 0 0 3 -3v-2a3 3 0 0 0 -3 -3h-2",
    ]),
    plugged: tb("#011899", [
      "M7 12l5 5l-1.5 1.5a3.536 3.536 0 1 1 -5 -5l1.5 -1.5",
      "M17 12l-5 -5l1.5 -1.5a3.536 3.536 0 1 1 5 5l-1.5 1.5",
      "M3 21l2.5 -2.5",
      "M18.5 5.5l2.5 -2.5",
      "M10 11l-2 2",
      "M13 14l-2 2",
    ]),
    peace: tb("#22d3d1", [
      "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0",
      "M12 3l0 18",
      "M12 12l6.3 6.3",
      "M12 12l-6.3 6.3",
    ]),
    thanks: tb("#fc0478", [
      "M19.5 12.572l-7.5 7.428l-7.5 -7.428a5 5 0 1 1 7.5 -6.566a5 5 0 1 1 7.5 6.572",
      "M12 6l-3.293 3.293a1 1 0 0 0 0 1.414l.543 .543c.69 .69 1.81 .69 2.5 0l1 -1a3.182 3.182 0 0 1 4.5 0l2.25 2.25",
      "M12.5 15.5l2 2",
      "M15 13l2 2",
    ]),
    joy: tb("#fbbf24", [
      "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0",
      "M9 9l.01 0",
      "M15 9l.01 0",
      "M8 13a4 4 0 1 0 8 0h-8",
    ]),
    wow: tb("currentColor", [
      "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0",
      "M9 9l.01 0",
      "M15 9l.01 0",
      "M12 15m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0",
    ]),
    blessed: tb("#fde68a", [
      "M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873z",
    ]),
    bible: tb("#ca8a04", [
      "M3 19a9 9 0 0 1 9 0a9 9 0 0 1 9 0",
      "M3 6a9 9 0 0 1 9 0a9 9 0 0 1 9 0",
      "M3 6l0 13",
      "M12 6l0 13",
      "M21 6l0 13",
    ]),
    faith: tb("currentColor", [
      "M10 21h4v-9h5v-4h-5v-5h-4v5h-5v4h5l0 9",
    ]),
    hope: tb("#86efac", [
      "M9 11a3 3 0 1 0 6 0a3 3 0 0 0 -6 0",
      "M17.657 16.657l-4.243 4.243a2 2 0 0 1 -2.827 0l-4.244 -4.243a8 8 0 1 1 11.314 0z",
    ]),
    following: tb("currentColor", [
      "M4 16.5a2.5 2.5 0 0 0 5 0a1.5 1.5 0 0 0 -1.5 -1.5h-2a1.5 1.5 0 0 0 -1.5 1.5",
      "M15 18.5a2.5 2.5 0 0 0 5 0a1.5 1.5 0 0 0 -1.5 -1.5h-2a1.5 1.5 0 0 0 -1.5 1.5",
      "M8.52 12h-4.04c-.348 0 -.678 -.179 -.823 -.496c-1.326 -2.904 -.774 -8.504 2.843 -8.504s4.17 5.6 2.843 8.504c-.145 .317 -.475 .496 -.824 .496",
      "M19.52 14h-4.04c-.348 0 -.678 -.179 -.823 -.496c-1.326 -2.904 -.774 -8.504 2.843 -8.504s4.17 5.6 2.843 8.504c-.145 .317 -.475 .496 -.824 .496",
    ]),
    checkmate: tb("currentColor", [
      "M8 16l-1.447 .724a1 1 0 0 0 -.553 .894v2.382h12v-2.382a1 1 0 0 0 -.553 -.894l-1.447 -.724h-8",
      "M9 3l1 3l-3.491 2.148a1 1 0 0 0 .524 1.852h2.967l-2.073 6h7.961l.112 -5c0 -3 -1.09 -5.983 -4 -7c-1.94 -.678 -2.94 -1.011 -3 -1",
    ]),
  };

  const reactions = [
    { key: "like", label: "Like" },
    { key: "love", label: "Love" },
    { key: "fire", label: "Fire" },
    { key: "pray", label: "Pray" },
    { key: "clap", label: "Clap" },
    { key: "amen", label: "Amen" },
    { key: "bless", label: "Bless" },
    { key: "inspired", label: "Inspired" },
    { key: "plugged", label: "Plugged" },
    { key: "peace", label: "Peace" },
    { key: "thanks", label: "Thanks" },
    { key: "joy", label: "Joy" },
    { key: "wow", label: "Wow" },
    { key: "blessed", label: "Blessed" },
    { key: "bible", label: "Bible" },
    { key: "faith", label: "Faith" },
    { key: "hope", label: "Hope" },
    { key: "following", label: "Following" },
    { key: "checkmate", label: "Checkmate" },
  ];

  window.CHURCHFORCE_REACTIONS = reactions;
  window.CHURCHFORCE_REACTION_MAP = Object.fromEntries(
    reactions.map(({ key }) => [key, SVG[key]])
  );
})();
