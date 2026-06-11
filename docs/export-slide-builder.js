/**
 * Builds one PPT slide per logical block. Injected by export-architecture-ppt.py.
 */
(() => {
  const BODY_H = 628;

  function initMeasure() {
    if (document.getElementById("ppt-measure-root")) return;
    const m = document.createElement("div");
    m.id = "ppt-measure-root";
    m.className = "ppt-slide-body";
    m.style.cssText =
      "position:fixed;left:-12000px;top:0;width:1244px;visibility:hidden;pointer-events:none;";
    document.body.appendChild(m);
  }

  function measureNode(node) {
    const root = document.getElementById("ppt-measure-root");
    root.innerHTML = "";
    const inner = document.createElement("div");
    inner.className = "ppt-slide-body-inner";
    inner.appendChild(node.cloneNode(true));
    root.appendChild(inner);
    return inner.scrollHeight;
  }

  function cloneForSlide(node) {
    const wrap = document.createElement("div");
    wrap.className = "ppt-slide-body-inner";
    if (node) wrap.appendChild(node.cloneNode(true));
    return wrap;
  }

  function fitBody(body) {
    const inner = body.querySelector(".ppt-slide-body-inner");
    if (!inner) return;
    inner.style.transform = "none";
    inner.style.width = "100%";
    const h = inner.scrollHeight;
    if (h > BODY_H) {
      const scale = BODY_H / h;
      inner.style.transformOrigin = "top left";
      inner.style.transform = `scale(${scale})`;
      inner.style.width = `${100 / scale}%`;
    }
  }

  function makeSlide(chromeSection, bodyNode) {
    const slide = document.createElement("div");
    slide.className = "ppt-export-slide";
    slide.innerHTML = `
      <div class="ppt-slide-chrome">
        <img class="ppt-slide-logo" src="${document.querySelector(".cover-page .senao-logo")?.src || ""}" alt="">
        <span class="ppt-slide-doc">Architecture Plan / Diagrams</span>
        <span class="ppt-slide-section">${chromeSection}</span>
      </div>
      <div class="ppt-slide-body"></div>
    `;
    const body = slide.querySelector(".ppt-slide-body");
    if (bodyNode) body.appendChild(bodyNode);
    fitBody(body);
    return slide;
  }

  function chapterLabel(sectionEl) {
    const lbl = sectionEl.querySelector(".sheet-header .section-label");
    return lbl ? lbl.textContent.trim() : "Section";
  }

  /** Pack child nodes into a shell that keeps context (layer label, block head, mega head). */
  function repack(shell, childNodes) {
    const out = shell.cloneNode(false);
    const keep = shell.querySelector(
      ":scope > .layer-label, :scope > .block-head, :scope > .mega-head, :scope > .testbed-unified-title"
    );
    if (keep) out.appendChild(keep.cloneNode(true));
    childNodes.forEach((n) => out.appendChild(n.cloneNode(true)));
    return out;
  }

  function greedyPack(shell, pieces, maxH = BODY_H) {
    if (!pieces.length) return [];
    const batches = [];
    let batch = [];

    const flush = () => {
      if (!batch.length) return;
      batches.push(repack(shell, batch));
      batch = [];
    };

    for (const piece of pieces) {
      const tryBatch = [...batch, piece];
      const candidate = repack(shell, tryBatch);
      if (measureNode(candidate) > maxH && batch.length) flush();
      batch.push(piece);
    }
    flush();
    return batches;
  }

  function directChildren(el, selector) {
    return [...el.querySelectorAll(`:scope > ${selector}`)];
  }

  function splitTableBlock(block) {
    const head = block.querySelector(":scope > .block-head");
    const table = block.querySelector("table");
    if (!table) return null;
    const rows = [...table.querySelectorAll("tbody tr")];
    if (rows.length <= 4) return null;

    const thead = table.querySelector("thead");
    const groups = [];
    for (let i = 0; i < rows.length; i += 4) groups.push(rows.slice(i, i + 4));

    return groups.map((group) => {
      const part = document.createElement("div");
      part.className = "block";
      if (head) part.appendChild(head.cloneNode(true));
      const body = document.createElement("div");
      body.className = "block-body";
      const t = document.createElement("table");
      t.className = table.className;
      if (thead) t.appendChild(thead.cloneNode(true));
      const tb = document.createElement("tbody");
      group.forEach((r) => tb.appendChild(r.cloneNode(true)));
      t.appendChild(tb);
      body.appendChild(t);
      part.appendChild(body);
      return part;
    });
  }

  function blockPartsFromBody(body) {
    const phaseCards = body.querySelectorAll(".phase-grid .phase-card");
    if (phaseCards.length > 1) {
      return [...phaseCards].map((card) => {
        const grid = document.createElement("div");
        grid.className = "phase-grid";
        grid.appendChild(card.cloneNode(true));
        return grid;
      });
    }

    const siteItems = body.querySelectorAll(".site-grid .site-item");
    if (siteItems.length > 1) {
      return [...siteItems].map((item) => {
        const grid = document.createElement("div");
        grid.className = "site-grid";
        grid.appendChild(item.cloneNode(true));
        return grid;
      });
    }

    const valCards = body.querySelectorAll(".val-grid .val-card");
    if (valCards.length > 1) {
      return [...valCards].map((card) => {
        const grid = document.createElement("div");
        grid.className = "val-grid";
        grid.appendChild(card.cloneNode(true));
        return grid;
      });
    }

    const flowSteps = body.querySelectorAll(".flow-grid .flow-step");
    if (flowSteps.length > 1) {
      const groups = [];
      for (let i = 0; i < flowSteps.length; i += 3) {
        const grid = document.createElement("div");
        grid.className = "flow-grid";
        flowSteps.slice(i, i + 3).forEach((s) => grid.appendChild(s.cloneNode(true)));
        groups.push(grid);
      }
      return groups;
    }

    const stackCards = body.querySelectorAll(".stack-row .stack-card");
    if (stackCards.length > 2) {
      const groups = [];
      for (let i = 0; i < stackCards.length; i += 2) {
        const row = document.createElement("div");
        row.className = "stack-row";
        stackCards.slice(i, i + 2).forEach((c) => row.appendChild(c.cloneNode(true)));
        groups.push(row);
      }
      return groups;
    }

    const figure = body.querySelector(":scope > .figure-wrap");
    if (figure) {
      const img = figure.querySelector("img, svg");
      const caption = figure.querySelector(".figure-caption, p");
      if (img && caption) {
        const imgWrap = document.createElement("div");
        imgWrap.className = "figure-wrap";
        imgWrap.appendChild(img.cloneNode(true));
        const capWrap = document.createElement("div");
        capWrap.className = "figure-wrap";
        capWrap.appendChild(caption.cloneNode(true));
        return [imgWrap, capWrap];
      }
    }

    return directChildren(body, "*");
  }

  function splitBlockBody(block) {
    const split = splitTableBlock(block);
    if (split) return split;

    const body = block.querySelector(":scope > .block-body");
    if (!body) return null;

    const kids = blockPartsFromBody(body);
    if (kids.length <= 1) return null;

    return greedyPack(block, kids);
  }

  function splitFrameworkBox(fwBox, layerShell) {
    const pieces = [];
    const title = fwBox.querySelector(":scope > .fw-title");
    const subtitle = fwBox.querySelector(":scope > .fw-subtitle");
    const header = document.createElement("div");
    header.className = "framework-box";
    if (title) header.appendChild(title.cloneNode(true));
    if (subtitle) header.appendChild(subtitle.cloneNode(true));
    pieces.push(header);

    const modules = [...fwBox.querySelectorAll(".modules-grid .module-card")];
    if (modules.length) {
      for (let i = 0; i < modules.length; i += 2) {
        const grid = document.createElement("div");
        grid.className = "framework-box";
        const mg = document.createElement("div");
        mg.className = "modules-grid";
        modules.slice(i, i + 2).forEach((m) => mg.appendChild(m.cloneNode(true)));
        grid.appendChild(mg);
        pieces.push(grid);
      }
    }

    ["bootstrap-row", "common-libs", "reporting-row"].forEach((cls) => {
      const el = fwBox.querySelector(`:scope > .${cls}`);
      if (el) {
        const wrap = document.createElement("div");
        wrap.className = "framework-box";
        wrap.appendChild(el.cloneNode(true));
        pieces.push(wrap);
      }
    });

    return greedyPack(layerShell, pieces);
  }

  function splitLayer(layer) {
    const label = layer.querySelector(":scope > .layer-label");

    const workflowRows = layer.querySelector(".workflow-rows");
    if (workflowRows) {
      const pieces = directChildren(workflowRows, "div");
      if (pieces.length > 1) return greedyPack(layer, pieces);
    }

    const singleRow = layer.querySelector(":scope > .workflow-row");
    if (singleRow) {
      const steps = [];
      let cur = singleRow.firstElementChild;
      while (cur) {
        if (cur.classList.contains("workflow-box")) {
          const group = [cur];
          const arrow = cur.nextElementSibling;
          if (arrow?.classList.contains("arrow-flex")) group.push(arrow);
          steps.push(group);
        }
        cur = cur.nextElementSibling;
      }
      if (steps.length > 1) {
        const rowPieces = steps.map((group) => {
          const row = document.createElement("div");
          row.className = "workflow-row";
          group.forEach((n) => row.appendChild(n.cloneNode(true)));
          return row;
        });
        return greedyPack(layer, rowPieces);
      }
    }

    const mid = layer.querySelector(":scope > .mid-section");
    if (mid) {
      const side = mid.querySelector(".side-col");
      const fw = mid.querySelector(".framework-box");
      const pieces = [];
      if (side) pieces.push(side);
      if (fw) return [...(side ? greedyPack(layer, [side]) : []), ...splitFrameworkBox(fw, layer)];
      if (pieces.length) return greedyPack(layer, pieces);
    }

    const lab = layer.querySelector(".lab-topology");
    if (lab) {
      const pieces = [];
      const unified = lab.querySelector(".testbed-unified");
      if (unified) {
        const trex = unified.querySelector(".phy-trex-wrap");
        const title = unified.querySelector(".testbed-unified-title");
        if (title || trex) {
          const top = document.createElement("div");
          top.className = "testbed-unified";
          if (title) top.appendChild(title.cloneNode(true));
          if (trex) top.appendChild(trex.cloneNode(true));
          const wrap = document.createElement("div");
          wrap.className = "lab-topology";
          wrap.appendChild(top);
          pieces.push(wrap);
        }
        const phyBody = unified.querySelector(".phy-body");
        if (phyBody) {
          directChildren(phyBody, ".phy-col").forEach((col) => {
            const wrap = document.createElement("div");
            wrap.className = "lab-topology";
            const tu = document.createElement("div");
            tu.className = "testbed-unified";
            const pb = document.createElement("div");
            pb.className = "phy-body";
            pb.appendChild(col.cloneNode(true));
            tu.appendChild(pb);
            wrap.appendChild(tu);
            pieces.push(wrap);
          });
          const dut = phyBody.querySelector(".phy-dut-band");
          if (dut) {
            const wrap = document.createElement("div");
            wrap.className = "lab-topology";
            const tu = document.createElement("div");
            tu.className = "testbed-unified";
            const pb = document.createElement("div");
            pb.className = "phy-body";
            pb.appendChild(dut.cloneNode(true));
            tu.appendChild(pb);
            wrap.appendChild(tu);
            pieces.push(wrap);
          }
        }
      }
      const hints = lab.querySelector(".topo-flow-hint");
      if (hints) {
        const wrap = document.createElement("div");
        wrap.className = "lab-topology";
        wrap.appendChild(hints.cloneNode(true));
        pieces.push(wrap);
      }
      if (pieces.length > 1) return greedyPack(layer, pieces);
    }

    const trafficCards = layer.querySelectorAll(".traffic-grid .traffic-card");
    if (trafficCards.length > 2) {
      return greedyPack(layer, [...trafficCards]);
    }

    const suites = layer.querySelectorAll(".suite-card, .suites-grid > *");
    if (suites.length > 2) {
      return greedyPack(layer, [...suites]);
    }

    if (label) {
      const bodyKids = [...layer.children].filter((c) => !c.classList.contains("layer-label"));
      if (bodyKids.length > 1) return greedyPack(layer, bodyKids);
    }

    return null;
  }

  function splitMega(mega) {
    const head = mega.querySelector(":scope > .mega-head");
    const cards = [...mega.querySelectorAll(".tools-grid .tool-card")];
    if (cards.length <= 4) return null;

    const groups = [];
    for (let i = 0; i < cards.length; i += 3) {
      const pack = document.createElement("div");
      pack.className = "mega-section ppt-mega-part";
      if (head) pack.appendChild(head.cloneNode(true));
      const grid = document.createElement("div");
      grid.className = "tools-grid";
      cards.slice(i, i + 3).forEach((c) => grid.appendChild(c.cloneNode(true)));
      const body = document.createElement("div");
      body.className = "mega-body";
      const sec = document.createElement("div");
      sec.className = "section";
      sec.appendChild(grid);
      body.appendChild(sec);
      pack.appendChild(body);
      groups.push(pack);
    }
    return groups;
  }

  function expandToSlides(node) {
    initMeasure();
    if (measureNode(node) <= BODY_H) return [node];

    let parts = null;
    if (node.classList?.contains("layer")) parts = splitLayer(node);
    else if (node.classList?.contains("block")) parts = splitBlockBody(node);
    else if (node.classList?.contains("mega-section")) parts = splitMega(node);

    if (!parts || parts.length <= 1) return [node];
    return parts.flatMap((p) => expandToSlides(p));
  }

  function unitsFromDiagram(container) {
    const units = [];
    const header = container.querySelector(":scope > .header");
    const legend = container.querySelector(":scope > .legend");
    if (header) {
      const opener = document.createElement("div");
      opener.className = "ppt-opener-block";
      opener.appendChild(header.cloneNode(true));
      if (legend) opener.appendChild(legend.cloneNode(true));
      units.push(opener);
    }
    container.querySelectorAll(":scope > .layer").forEach((layer) => {
      units.push(layer.cloneNode(true));
    });
    container.querySelectorAll(":scope > .mega-section").forEach((mega) => {
      const sections = mega.querySelectorAll(":scope > .mega-body > .section");
      if (sections.length > 1) {
        const head = mega.querySelector(":scope > .mega-head");
        sections.forEach((sec) => {
          const pack = document.createElement("div");
          pack.className = "mega-section ppt-mega-part";
          if (head) pack.appendChild(head.cloneNode(true));
          pack.appendChild(sec.cloneNode(true));
          units.push(pack);
        });
      } else {
        units.push(mega.cloneNode(true));
      }
    });
    const footer = container.querySelector(":scope > .footer");
    if (footer) units.push(footer.cloneNode(true));
    return units;
  }

  function unitsFromPage(page) {
    const units = [];
    const disclaimer = page.querySelector(":scope > .disclaimer");
    const hero = page.querySelector(":scope > .hero, :scope > .header");
    if (disclaimer || hero) {
      const opener = document.createElement("div");
      opener.className = "ppt-opener-block";
      if (disclaimer) opener.appendChild(disclaimer.cloneNode(true));
      if (hero) opener.appendChild(hero.cloneNode(true));
      units.push(opener);
    }
    page.querySelectorAll(":scope > .block").forEach((block) => {
      units.push(block.cloneNode(true));
    });
    const footer = page.querySelector(":scope > .footer");
    if (footer) units.push(footer.cloneNode(true));
    return units;
  }

  const root = document.createElement("div");
  root.id = "ppt-export-root";

  const cover = document.querySelector(".cover-page");
  if (cover) {
    const slide = document.createElement("div");
    slide.className = "ppt-export-slide ppt-export-slide--cover";
    slide.appendChild(cover.cloneNode(true));
    root.appendChild(slide);
  }

  const index = document.querySelector(".index-page");
  if (index) {
    const slide = document.createElement("div");
    slide.className = "ppt-export-slide ppt-export-slide--index";
    const idxClone = index.cloneNode(true);
    const nested = idxClone.querySelector(".sheet-header");
    if (nested) nested.remove();
    slide.appendChild(idxClone);
    root.appendChild(slide);
  }

  document.querySelectorAll(".compiled-section").forEach((sectionEl) => {
    const chapter = chapterLabel(sectionEl);
    const inner = sectionEl.querySelector(".section-inner");
    if (!inner) return;

    const diagram = inner.querySelector(":scope > .diagram-container");
    const page = inner.querySelector(":scope > .page");

    let units = [];
    if (diagram) units = unitsFromDiagram(diagram);
    else if (page) units = unitsFromPage(page);

    units.flatMap((unit) => expandToSlides(unit)).forEach((unit) => {
      root.appendChild(makeSlide(chapter, cloneForSlide(unit)));
    });
  });

  document.body.appendChild(root);
  document.body.classList.add("ppt-export-mode");

  window.__pptResult = {
    slideCount: root.querySelectorAll(".ppt-export-slide").length,
    labels: Array.from(root.querySelectorAll(".ppt-slide-section")).map((el) => el.textContent.trim()),
  };
})();
