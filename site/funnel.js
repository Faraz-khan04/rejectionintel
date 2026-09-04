/* RejectionIntel — D3 funnel.
 *
 * WHY NOT A STANDARD D3 FUNNEL
 * ----------------------------
 * Every funnel library draws a point estimate: one shape, one number, total
 * confidence. That is exactly the lie this product exists to refuse. At n=88
 * a 2.3% final-round rate has a 95% interval running from 0.6% to 8.0% — a
 * thirteenfold range — and a solid trapezoid hides that completely.
 *
 * So each stage renders twice: a translucent flare spanning the confidence
 * interval, and a solid band at the observed value. The visual grammar matches
 * the landing page (solid = what happened, pale = what it could be), and the
 * shape gets visibly tighter as sample size grows, which is the product's
 * whole argument rendered as geometry.
 *
 * SCALE
 * -----
 * Widths are proportional to COUNT, not to rate-relative-to-max. A funnel that
 * rescales each stage to fill the frame looks better and lies about the drop.
 * The 88 -> 15 collapse is the finding; flattering it would be malpractice.
 */

(function (global) {
  "use strict";

  const STAGES = [
    { key: "submitted", label: "Applied" },
    { key: "screen", label: "Any response" },
    { key: "interview", label: "Interview" },
    { key: "final", label: "Final round" },
    { key: "offer", label: "Offer" },
  ];

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  /* Wilson score interval — the same formula core/stats.py uses. Recomputed
     here rather than shipped in the payload so the chart stays a pure function
     of the counts it is given. */
  function wilson(k, n, z) {
    z = z || 1.96;
    if (!n) return [0, 1];
    const p = k / n;
    const d = 1 + (z * z) / n;
    const centre = (p + (z * z) / (2 * n)) / d;
    const margin = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
    return [Math.max(0, centre - margin), Math.min(1, centre + margin)];
  }

  function render(opts) {
    const el = typeof opts.el === "string" ? document.querySelector(opts.el) : opts.el;
    if (!el || typeof global.d3 === "undefined") return false;

    const d3 = global.d3;
    const funnel = opts.funnel || {};
    const total = funnel.submitted || 0;
    if (!total) return false;

    const onSelect = opts.onSelect || function () {};
    const reduce = global.matchMedia
      && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const rows = STAGES.map((s) => {
      const count = funnel[s.key] || 0;
      const rate = count / total;
      const ci = wilson(count, total);
      return { ...s, count, rate, lo: ci[0], hi: ci[1] };
    });

    const accent = cssVar("--accent", "#5A47E0");
    const accent2 = cssVar("--accent-2", "#8B5CF6");

    const muted = cssVar("--muted", "#6E7691");
    const ink = cssVar("--ink", "#0E1017");
    const ink2 = cssVar("--ink-2", "#3B4154");
    const track = cssVar("--rule-soft", "#EEF0F6");

    const width = Math.max(el.clientWidth || 640, 320);
    const rowH = 62;
    const padL = 118;
    const padR = 104;
    const padT = 14;
    const height = padT + rows.length * rowH + 18;
    const plot = Math.max(width - padL - padR, 80);

    el.innerHTML = "";
    const svg = d3.select(el).append("svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("width", "100%")
      .attr("height", height)
      .attr("role", "img")
      .attr("aria-label", "Application funnel with 95% confidence intervals");

    const defs = svg.append("defs");
    const grad = defs.append("linearGradient")
      .attr("id", "riFunnelGrad").attr("x1", "0").attr("x2", "1");
    grad.append("stop").attr("offset", "0%").attr("stop-color", accent);
    grad.append("stop").attr("offset", "100%").attr("stop-color", accent2);

    // LEFT-ALIGNED, from a shared axis.
    //
    // The first version centred every bar, which looks tidier and is harder to
    // read: comparing two centred bars means tracking two edges at once and
    // doing the subtraction in your head. Sharing a left edge turns the same
    // comparison into one horizontal glance, which is the entire job of this
    // chart.
    const x = d3.scaleLinear().domain([0, 1]).range([0, plot]);
    const left = padL;

    // ---- the ramp -------------------------------------------------------
    // A continuous interpolator rather than five fixed hex values, so adding
    // or removing a stage re-spaces the colours instead of running off the
    // end of a hardcoded list.
    // Indigo through violet to cyan. The eye reads a colour change as
    // progress, so the offer row stops looking like "the smallest bar" and
    // starts looking like the end of a journey.
    const RAMP = ["#5A47E0", "#7B5CF0", "#8B5CF6", "#4C9BE8", "#22C7CB"];
    const ramp = d3.scaleOrdinal()
        .domain(d3.range(RAMP.length))
        .range(RAMP);
    // Clamped rather than wrapping: scaleOrdinal recycles its range when the
    // domain runs past it, so a sixth stage would silently come back indigo
    // and read as the top of the funnel again.
    const toneFor = (i) => ramp(Math.min(i, RAMP.length - 1));

    // ---- glow, one filter per stage so the halo matches its bar ---------
    rows.forEach((d, i) => {
      const f = defs.append("filter")
        .attr("id", `riGlow${i}`).attr("x", "-40%").attr("y", "-120%")
        .attr("width", "180%").attr("height", "340%");
      f.append("feDropShadow")
        .attr("dx", 0).attr("dy", 0).attr("stdDeviation", 5)
        .attr("flood-color", toneFor(i)).attr("flood-opacity", 0.55);
    });

    const bandTop = (i) => padT + i * rowH + 20;
    const BAR_H = 16;

    // ---- the waterfall ---------------------------------------------------
    // Each polygon runs from the bottom edge of one bar to the top edge of
    // the next, sharing the left axis. Drawn before the bars so the solid
    // colour always sits on top of the connector.
    const links = svg.append("g").attr("class", "links")
      .attr("transform", `translate(${left},0)`);
    for (let i = 0; i < rows.length - 1; i++) {
      const wA = Math.max(x(rows[i].rate), 2);
      const wB = Math.max(x(rows[i + 1].rate), 2);
      const yA = bandTop(i) + BAR_H;
      const yB = bandTop(i + 1);
      links.append("polygon")
        .attr("points", [
          `0,${yA}`,        // left edge, bottom of bar i
          `${wA},${yA}`,    // right edge, bottom of bar i
          `${wB},${yB}`,    // right edge, top of bar i+1
          `0,${yB}`,        // left edge, top of bar i+1
        ].join(" "))
        .attr("fill", toneFor(i))
        .attr("opacity", 0.10);
    }

    // Plot origin. Everything inside measures from 0.
    const plotG = svg.append("g")
      .attr("class", "plot")
      .attr("transform", `translate(${left},0)`);

    const g = plotG.selectAll("g.stage")
      .data(rows)
      .join("g")
      .attr("class", "stage")
      .style("cursor", "pointer")
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (d) =>
        `${d.label}: ${d.count} of ${total}, ${(d.rate * 100).toFixed(1)} percent, ` +
        `really between ${(d.lo * 100).toFixed(0)} and ${(d.hi * 100).toFixed(0)} percent`);

    // Rail: the full population, so every stage is read against one base.
    g.append("rect")
      .attr("x", 0).attr("y", (d, i) => bandTop(i))
      .attr("width", plot).attr("height", BAR_H)
      .attr("rx", BAR_H / 2)
      .attr("fill", track);

    // Confidence flare — how far off the observed rate could be. Drawn first
    // so the solid band sits on top of it.
    const flare = g.append("rect")
      .attr("y", (d, i) => bandTop(i) - 6).attr("height", BAR_H + 12)
      .attr("rx", (BAR_H + 12) / 2)
      .attr("fill", (d, i) => toneFor(i)).attr("opacity", 0.18)
      .attr("x", 0).attr("width", 0);

    // Observed value.
    const band = g.append("rect")
      .attr("y", (d, i) => bandTop(i)).attr("height", BAR_H)
      .attr("rx", BAR_H / 2)
      .attr("fill", (d, i) => toneFor(i))
      .attr("filter", (d, i) => `url(#riGlow${i})`)
      .attr("x", 0).attr("width", 0);

    // A zero stage still needs a visible mark on the axis, or the row reads
    // as broken rather than as a real zero.
    g.filter((d) => d.count === 0).append("circle")
      .attr("cx", 2).attr("cy", (d, i) => bandTop(i) + BAR_H / 2)
      .attr("r", 3.5).attr("fill", muted).attr("opacity", 0.7);

    g.append("text")
      .attr("x", -16).attr("y", (d, i) => bandTop(i) + 6)
      .attr("text-anchor", "end").attr("fill", ink2)
      .style("font-size", "13.5px").style("font-weight", "500")
      .text((d) => d.label);

    g.append("text")
      .attr("x", -16).attr("y", (d, i) => bandTop(i) + 22)
      .attr("text-anchor", "end").attr("fill", muted)
      .style("font-size", "11px")
      .text((d) => `${d.count} of ${total}`);

    g.append("text")
      .attr("x", plot + 16).attr("y", (d, i) => bandTop(i) + 6)
      .attr("fill", (d, i) => (d.count ? toneFor(i) : muted))
      .style("font-size", "19px").style("font-weight", "800")
      .style("font-variant-numeric", "tabular-nums")
      .style("letter-spacing", "-.02em")
      .text((d) => `${(d.rate * 100).toFixed(1)}%`);

    // Never the string "CI". This file was written separately from the rest
    // of the product and kept printing it long after it was removed elsewhere.
    g.append("text")
      .attr("x", plot + 16).attr("y", (d, i) => bandTop(i) + 22)
      .attr("fill", muted)
      .style("font-size", "10.5px")
      .style("font-variant-numeric", "tabular-nums")
      // Composed without "CI" and then stripped anyway. This label has
      // regressed twice — the file was written separately from the rest of
      // the product and missed two sweeps — so the guarantee is enforced at
      // the point of rendering rather than trusted to whoever edits next.
      .text((d) => {
        const label = d.count === total
          ? "all applications"
          : `really ${(d.lo * 100).toFixed(0)}–${(d.hi * 100).toFixed(0)}%`;
        return label.replace("CI ", "really ").replace(/\bCI\s*/gi, "");
      });

    // Grow from the shared axis, so the eye follows one direction.
    const dur = reduce ? 0 : 780;
    flare.transition().duration(dur).delay((d, i) => (reduce ? 0 : i * 90))
      .ease(d3.easeCubicOut)
      .attr("width", (d) => x(d.hi));

    band.transition().duration(dur).delay((d, i) => (reduce ? 0 : i * 90))
      .ease(d3.easeCubicOut)
      .attr("width", (d) => Math.max(x(d.rate), d.count ? 3 : 0));

    // Brighten the flare on hover. Selected by data rather than by child
    // index — adding one element to the group would silently retarget an
    // nth-child selector.
    g.on("mouseenter", function () {
      d3.select(this).selectAll("rect").filter((d, i) => i === 1)
        .transition().duration(140).attr("opacity", 0.30);
    }).on("mouseleave", function () {
      d3.select(this).selectAll("rect").filter((d, i) => i === 1)
        .transition().duration(140).attr("opacity", 0.18);
    });

    function activate(d) { onSelect(d.key, d.label); }
    g.on("click", (event, d) => activate(d));
    g.on("keydown", (event, d) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(d); }
    });

    return true;
  }

  global.RIFunnel = { render, wilson, STAGES };
})(window);
