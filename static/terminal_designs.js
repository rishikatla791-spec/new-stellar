/* Designs for the Stellar Console: colour text art printed into the
 * terminal. A gallery of pieces drawn for Stellar, big block-letter text,
 * and any picture turned into coloured ASCII art, each in a gradient.
 *
 * The art is written into the terminal's display, not sent to the sandbox:
 * nothing runs, and the line being typed is put back underneath it. Loaded
 * after main.js, whose termState, termEl, termNote and fitTerminal it uses.
 */
"use strict";

(() => {
  const ESC = "\x1b";
  const RESET = ESC + "[0m";

  /* ---- the gallery --------------------------------------------------- */
  // Each piece is a left half mirrored round a centre column, so it is
  // exactly symmetrical.
  const MIRROR = { "/": "\\", "\\": "/", "(": ")", ")": "(", "<": ">", ">": "<",
                   "[": "]", "]": "[", "{": "}", "}": "{", "`": "'" };
  const mirror = (s) => [...s].reverse().map((c) => MIRROR[c] || c).join("");
  const sym = (rows) => rows.map(([left, mid]) => left + mid + mirror(left));

  const GALLERY = {
    star: sym([
      ["            ", "."], ["            ", "|"], ["     .      ", "|"],
      ["      \\     ", "|"], ["       \\   _", "|"], ["        \\ / ", "|"],
      ["   ------( -", "+"], ["        / \\_", "|"], ["       /    ", "|"],
      ["      /     ", "|"], ["     '      ", "|"], ["            ", "|"],
      ["            ", "'"],
    ]),
    butterfly: sym([
      ["   _.-''-._    \\  ", " "], [" .'  .--.  '.   \\ ", " "],
      ["/   ( () )   \\   \\", " "], ["|    '--'     '._ ", "Y"],
      ["\\    .--.        '", "#"], [" '._( () )   _.-'/", "#"],
      ["    '-..-'.-'   / ", "#"], ["     .'  /     /  ", "#"],
      ["    / .-.     |   ", "'"], ["   | ( o )    |   ", " "],
      ["    \\ '-' _.-'    ", " "], ["     '-.-'        ", " "],
    ]),
    rocket: sym([
      ["       ", "^"], ["      /", " "], ["     / ", " "], ["    |  ", " "],
      ["    | (", "o"], ["    |  ", " "], ["    | (", "o"], ["    |  ", " "],
      ["   /|  ", " "], ["  / |  ", " "], [" /__|__", "_"], ["    \\_/", " "],
      ["     ( ", ")"], ["      (", " "], ["       ", "'"],
    ]),
    planet: sym([
      ["          .--'''", "'"], ["       .''      ", " "],
      ["  .---/---------", "-"], [" (   |          ", " "],
      ["  '--|----------", "-"], ["      \\         ", " "],
      ["       '.       ", " "], ["          '-...", "."],
    ]),
  };

  /* ---- big block letters ---------------------------------------------- */
  const FONT = {
    A: [" ███ ", "█   █", "█████", "█   █", "█   █"], B: ["████ ", "█   █", "████ ", "█   █", "████ "],
    C: [" ████", "█    ", "█    ", "█    ", " ████"], D: ["████ ", "█   █", "█   █", "█   █", "████ "],
    E: ["█████", "█    ", "████ ", "█    ", "█████"], F: ["█████", "█    ", "████ ", "█    ", "█    "],
    G: [" ████", "█    ", "█  ██", "█   █", " ████"], H: ["█   █", "█   █", "█████", "█   █", "█   █"],
    I: ["█████", "  █  ", "  █  ", "  █  ", "█████"], J: ["█████", "    █", "    █", "█   █", " ███ "],
    K: ["█   █", "█  █ ", "███  ", "█  █ ", "█   █"], L: ["█    ", "█    ", "█    ", "█    ", "█████"],
    M: ["█   █", "██ ██", "█ █ █", "█   █", "█   █"], N: ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    O: [" ███ ", "█   █", "█   █", "█   █", " ███ "], P: ["████ ", "█   █", "████ ", "█    ", "█    "],
    Q: [" ███ ", "█   █", "█ █ █", "█  █ ", " ██ █"], R: ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    S: [" ████", "█    ", " ███ ", "    █", "████ "], T: ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    U: ["█   █", "█   █", "█   █", "█   █", " ███ "], V: ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    W: ["█   █", "█   █", "█ █ █", "██ ██", "█   █"], X: ["█   █", " █ █ ", "  █  ", " █ █ ", "█   █"],
    Y: ["█   █", " █ █ ", "  █  ", "  █  ", "  █  "], Z: ["█████", "   █ ", "  █  ", " █   ", "█████"],
    0: [" ███ ", "█  ██", "█ █ █", "██  █", " ███ "], 1: ["  █  ", " ██  ", "  █  ", "  █  ", " ███ "],
    2: [" ███ ", "█   █", "  ██ ", " █   ", "█████"], 3: ["████ ", "    █", " ███ ", "    █", "████ "],
    4: ["█   █", "█   █", "█████", "    █", "    █"], 5: ["█████", "█    ", "████ ", "    █", "████ "],
    6: [" ███ ", "█    ", "████ ", "█   █", " ███ "], 7: ["█████", "    █", "   █ ", "  █  ", "  █  "],
    8: [" ███ ", "█   █", " ███ ", "█   █", " ███ "], 9: [" ███ ", "█   █", " ████", "    █", " ███ "],
    " ": ["   ", "   ", "   ", "   ", "   "], "!": ["█", "█", "█", " ", "█"],
    ".": [" ", " ", " ", " ", "█"], "-": ["    ", "    ", "████", "    ", "    "],
    "?": [" ███ ", "█   █", "  ██ ", "     ", "  █  "],
  };

  function bigText(text) {
    const glyphs = [...text.toUpperCase()].map((ch) => FONT[ch]).filter(Boolean);
    if (!glyphs.length) return null;
    return [0, 1, 2, 3, 4].map((row) => glyphs.map((g) => g[row]).join(" "));
  }

  /* ---- colour ---------------------------------------------------------- */
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const PALETTES = {
    night: ["#7df9ff", "#6d8cff", "#b78cff"],
    ocean: ["#9ff3ff", "#00b7ff", "#1f4dff"],
    sunset: ["#ffd166", "#ff7b54", "#ff3d81"],
    matrix: ["#b9ffd0", "#00ff66", "#00a843"],
    fire: ["#fff35c", "#ff9a1f", "#ff2d2d"],
    rainbow: ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#00c7be", "#007aff", "#af52de"],
  };

  function colourAt(stops, t) {
    const pos = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
    const i = Math.min(Math.floor(pos), stops.length - 2);
    const f = pos - i;
    const a = hex(stops[i]), b = hex(stops[i + 1]);
    return a.map((v, k) => Math.round(v + (b[k] - v) * f));
  }
  const fg = ([r, g, b]) => `${ESC}[38;2;${r};${g};${b}m`;

  // A diagonal sweep across the piece; spaces stay uncoloured.
  function paint(lines, paletteName) {
    const stops = PALETTES[paletteName] || PALETTES.night;
    const width = Math.max(...lines.map((l) => [...l].length));
    const span = width + lines.length * 2;
    return lines.map((line, row) => [...line].map((ch, col) =>
      ch === " " ? ch : fg(colourAt(stops, (col + row * 2) / span)) + ch).join("") + RESET);
  }

  /* ---- a picture as ASCII art ------------------------------------------ */
  // Sparse to dense. Brightness is lifted (a gamma under 1) before it picks
  // a character: on a black terminal, mid-tones mapped straight came out as
  // thin dashes, and the colour of a picture barely showed.
  const RAMP = " .,:;i1tfLCG08@";
  const LIFT = 0.65;

  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That file isn't a picture this browser can read.")); };
      img.src = url;
    });
  }

  // Terminal cells are about twice as tall as they are wide, so a picture
  // is sampled at half the rows its width would suggest.
  async function imageArt(file, cols, paletteName) {
    const img = await loadImage(file);
    let w = Math.max(20, Math.min(cols - 1, 120));
    let h = Math.round(w * (img.naturalHeight / img.naturalWidth) * 0.5);
    if (h > 60) { w = Math.max(20, Math.round(w * 60 / h)); h = 60; }
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, w, h);
    const px = ctx.getImageData(0, 0, w, h).data;
    const own = paletteName === "image";
    const lines = [];
    for (let y = 0; y < h; y++) {
      let line = "";
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const [r, g, b, a] = [px[i], px[i + 1], px[i + 2], px[i + 3] / 255];
        const light = Math.pow(((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255) * a, LIFT);
        const ch = RAMP[Math.min(RAMP.length - 1, Math.round(light * (RAMP.length - 1)))];
        if (ch === " ") { line += " "; continue; }
        line += (own ? fg([r, g, b]) : "") + ch;
      }
      lines.push(own ? line + RESET : line);
    }
    return own ? lines : paint(lines, paletteName);
  }

  /* ---- printing ---------------------------------------------------------- */
  const visibleWidth = (s) => [...s.replace(/\x1b\[[0-9;]*m/g, "")].length;

  // Written to the display, then the line being typed is put back under
  // the art, so the shell's prompt carries on as if nothing happened.
  function print(lines) {
    const term = termState.term;
    if (!term) { termNote("Open the terminal first"); return false; }
    if (term.buffer.active.type === "alternate") {
      termNote("A full-screen program is running: close it first");
      return false;
    }
    const width = Math.max(...lines.map(visibleWidth));
    if (width > term.cols) {
      termNote(`Too wide: needs ${width} columns, this window has ${term.cols}. Try full screen.`);
      return false;
    }
    const buf = term.buffer.active;
    const current = buf.getLine(buf.baseY + buf.cursorY);
    const typed = current ? current.translateToString(true, 0, buf.cursorX) : "";
    term.write("\r\n" + lines.join("\r\n") + RESET + "\r\n\r\n" + typed);
    term.focus();
    return true;
  }

  /* ---- the Design bar ----------------------------------------------------- */
  const ui = {
    button: document.getElementById("terminal-design-btn"),
    bar: document.getElementById("terminal-design"),
    kind: document.getElementById("design-kind"),
    text: document.getElementById("design-text"),
    palette: document.getElementById("design-palette"),
    printBtn: document.getElementById("design-print"),
    image: document.getElementById("design-image"),
  };
  if (!ui.button || !ui.bar) return;

  function syncForm() {
    const kind = ui.kind.value;
    ui.text.hidden = kind !== "text";
    const imageOption = ui.palette.querySelector('option[value="image"]');
    imageOption.hidden = kind !== "image";
    if (kind === "image") ui.palette.value = "image";
    else if (ui.palette.value === "image") ui.palette.value = "night";
    ui.printBtn.textContent = kind === "image" ? "Choose picture" : "Print";
  }

  function toggleBar(open) {
    const show = open === undefined ? ui.bar.hidden : open;
    ui.bar.hidden = !show;
    ui.button.setAttribute("aria-expanded", String(show));
    requestAnimationFrame(() => {
      fitTerminal();
      if (show) (ui.kind.value === "text" ? ui.text : ui.kind).focus();
    });
  }

  function printChosen() {
    const kind = ui.kind.value;
    if (kind === "image") { ui.image.click(); return; }
    let lines;
    if (kind === "text") {
      lines = bigText(ui.text.value.trim());
      if (!lines) { termNote("Type some letters or numbers first"); ui.text.focus(); return; }
    } else {
      lines = GALLERY[kind];
    }
    print(paint(lines, ui.palette.value));
  }

  ui.button.addEventListener("click", () => toggleBar());
  ui.kind.addEventListener("change", syncForm);
  ui.printBtn.addEventListener("click", printChosen);
  ui.text.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); printChosen(); } });
  ui.image.addEventListener("change", async () => {
    const file = ui.image.files[0];
    ui.image.value = "";
    if (!file || !termState.term) return;
    termNote("Drawing the picture…", { sticky: true });
    try {
      if (print(await imageArt(file, termState.term.cols, ui.palette.value))) termNote("Picture drawn");
    } catch (err) {
      termNote(err.message);
    }
  });
  syncForm();

  // For checks from the browser console and tests.
  window.stellarDesigns = { GALLERY, bigText, paint, imageArt, print };
})();
