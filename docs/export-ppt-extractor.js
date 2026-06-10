/**
 * Extracts architecture-plan.html into slide-ready JSON for native PPT build.
 */
(() => {
  const txt = (el) => (el ? el.innerText.replace(/\s+/g, " ").trim() : "");

  function chipsFrom(container, sel) {
    return [...(container?.querySelectorAll(sel) || [])].map((c) => txt(c)).filter(Boolean);
  }

  function bulletsFrom(ul) {
    if (!ul) return [];
    return [...ul.querySelectorAll("li")].map((li) => txt(li)).filter(Boolean);
  }

  function extractWorkflowBox(box) {
    const title = txt(box.querySelector(".wf-header")) || txt(box.querySelector("h4"));
    const items = bulletsFrom(box.querySelector(".wf-list"));
    const pipes = [...(box.querySelectorAll(".jenkins-pipes .pipe-item, .pipe-list li") || [])].map((p) =>
      txt(p)
    );
    return { title, items: [...items, ...pipes].filter(Boolean) };
  }

  function paginateSlides(slides, chunkSize, build) {
    const out = [];
    for (let i = 0; i < slides.length; i += chunkSize) {
      const chunk = slides.slice(i, i + chunkSize);
      out.push(build(chunk, i > 0 ? Math.floor(i / chunkSize) + 1 : 0));
    }
    return out;
  }

  function extractLayer(layer) {
    const label = txt(layer.querySelector(":scope > .layer-label"));
    const slides = [];

    const rowLabel = layer.querySelector(".workflow-row-label");
    const workflowRows = layer.querySelectorAll(".workflow-row");
    if (workflowRows.length) {
      const groups = [];
      layer.querySelectorAll(".workflow-rows > div").forEach((grp) => {
        const sub = txt(grp.querySelector(".workflow-row-label"));
        const steps = [...grp.querySelectorAll(".workflow-box")].map(extractWorkflowBox);
        if (steps.length) groups.push({ subtitle: sub, steps });
      });
      if (!groups.length) {
        const steps = [...workflowRows].flatMap((row) =>
          [...row.querySelectorAll(".workflow-box")].map(extractWorkflowBox)
        );
        groups.push({ subtitle: rowLabel ? txt(rowLabel) : "", steps });
      }
      groups.forEach((g) => {
        const stepCards = g.steps.map((s) => ({
          title: s.title,
          bullets: s.items,
        }));
        paginateSlides(stepCards, 3, (chunk, part) => {
          slides.push({
            type: "cards",
            title: label,
            subtitle: [g.subtitle, part ? `(continued ${part})` : ""].filter(Boolean).join(" ") || undefined,
            cards: chunk,
          });
        });
      });
      return slides;
    }

    const suiteCards = layer.querySelectorAll(".suite-card");
    if (suiteCards.length) {
      const cards = [...suiteCards].map((c) => ({
        title: txt(c.querySelector("h4, .suite-title")),
        body: txt(c.querySelector("p, .suite-desc")),
        bullets: bulletsFrom(c.querySelector("ul")),
      }));
      paginateSlides(cards, 4, (chunk, part) => {
        slides.push({
          type: "cards",
          title: part ? `${label} (continued)` : label,
          cards: chunk,
        });
      });
      return slides;
    }

    const trafficCards = layer.querySelectorAll(".traffic-card");
    if (trafficCards.length) {
      const cards = [...trafficCards].map((c) => {
        const badge = txt(c.querySelector(".badge"));
        const title = txt(c.querySelector("h3, h4"));
        return {
          title: badge ? `${title} · ${badge}` : title,
          body: "",
          bullets: bulletsFrom(c.querySelector("ul")),
        };
      });
      paginateSlides(cards, 4, (chunk, part) => {
        slides.push({
          type: "cards",
          title: part ? `${label} (continued)` : label,
          cards: chunk,
        });
      });
      return slides;
    }

    const moduleCards = layer.querySelectorAll(".module-card");
    if (moduleCards.length) {
      const cards = [...moduleCards].map((c) => ({
        title: txt(c.querySelector("h4")),
        body: [txt(c.querySelector(".sub")), txt(c.querySelector("p"))].filter(Boolean).join(" — "),
        bullets: [],
      }));
      paginateSlides(cards, 4, (chunk, part) => {
        slides.push({
          type: "cards",
          title: part ? `${label} (continued)` : label,
          cards: chunk,
        });
      });
      const extras = [];
      layer.querySelectorAll(".bootstrap-card, .side-box h3").forEach((el) => {
        const card = el.closest(".bootstrap-card, .side-box");
        if (card && !extras.includes(card)) extras.push(card);
      });
      extras.forEach((card) => {
        slides.push({
          type: "bullets",
          title: label,
          subtitle: txt(card.querySelector("h3, h4")),
          items: bulletsFrom(card.querySelector("ul")).length
            ? bulletsFrom(card.querySelector("ul"))
            : [txt(card.querySelector("p"))].filter(Boolean),
        });
      });
      return slides;
    }

    if (layer.classList.contains("layer-lab") || layer.querySelector(".lab-topology")) {
      slides.push({ type: "visual", title: label, selector: uniqueSelector(layer) });
      return slides;
    }

    slides.push({
      type: "bullets",
      title: label,
      items: [txt(layer)].slice(0, 12),
    });
    return slides;
  }

  function uniqueSelector(el) {
    if (el.id) return `#${el.id}`;
    const path = [];
    let node = el;
    while (node && node !== document.body) {
      let seg = node.tagName.toLowerCase();
      if (node.classList.length) seg += "." + [...node.classList].slice(0, 2).join(".");
      const parent = node.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (sibs.length > 1) seg += `:nth-child(${[...parent.children].indexOf(node) + 1})`;
      }
      path.unshift(seg);
      node = parent;
    }
    return path.join(" > ");
  }

  function extractMegaSection(mega) {
    const head = txt(mega.querySelector(":scope > .mega-head, :scope > .mega-title"));
    const slides = [];
    const sections = mega.querySelectorAll(":scope > .mega-body > .section");
    const targets = sections.length ? [...sections] : [mega];

    targets.forEach((sec) => {
      const secTitle = txt(sec.querySelector(".section-title, h3")) || head;
      const toolCards = sec.querySelectorAll(".tool-card");
      if (toolCards.length) {
        const cards = [...toolCards].map((c) => ({
          title: txt(c.querySelector("h4, .tool-name, strong")),
          body: txt(c.querySelector("p, span")),
          bullets: bulletsFrom(c.querySelector("ul")),
        }));
        paginateSlides(cards, 6, (chunk, part) => {
          const sub = secTitle !== head ? secTitle : "";
          slides.push({
            type: "cards",
            title: head,
            subtitle: [sub, part ? `(continued ${part})` : ""].filter(Boolean).join(" ") || undefined,
            cards: chunk,
          });
        });
        return;
      }
      slides.push({
        type: "bullets",
        title: head,
        subtitle: secTitle !== head ? secTitle : undefined,
        items: bulletsFrom(sec.querySelector("ul")).length
          ? bulletsFrom(sec.querySelector("ul"))
          : [txt(sec)].filter((t) => t.length < 500),
      });
    });
    return slides;
  }

  function extractBlock(block) {
    const title = txt(block.querySelector(".block-head"));
    const body = block.querySelector(".block-body");
    if (!body) return [{ type: "bullets", title, items: [] }];

    const table = body.querySelector("table");
    if (table) {
      const headers = [...table.querySelectorAll("thead th")].map((th) => txt(th));
      const rows = [...table.querySelectorAll("tbody tr")].map((tr) =>
        [...tr.querySelectorAll("td")].map((td) => txt(td))
      );
      const slides = [];
      paginateSlides(rows, 5, (chunk, part) => {
        slides.push({
          type: "table",
          title: part ? `${title} (continued)` : title,
          headers,
          rows: chunk,
        });
      });
      return slides.length ? slides : [{ type: "table", title, headers, rows: [] }];
    }

    const img = body.querySelector("img");
    if (img?.src) {
      return [
        { type: "image", title, src: img.src, caption: txt(body.querySelector(".figure-caption, p")) },
      ];
    }

    const svg = body.querySelector("svg");
    if (svg) {
      return [{ type: "visual", title, selector: uniqueSelector(block) }];
    }

    const siteItems = body.querySelectorAll(".site-item");
    if (siteItems.length) {
      const cards = [...siteItems].map((it) => ({
        title: txt(it.querySelector("h4")),
        body: txt(it.querySelector("p")),
        bullets: [],
      }));
      return [{ type: "cards", title, cards }];
    }

    const stackCards = body.querySelectorAll(".stack-card");
    if (stackCards.length) {
      const cards = [...stackCards].map((c) => ({
        title: txt(c.querySelector("h4, strong")),
        body: txt(c.querySelector("p")),
        bullets: bulletsFrom(c.querySelector("ul")),
      }));
      return [{ type: "cards", title, cards }];
    }

    const actCards = body.querySelectorAll(".act-card");
    if (actCards.length) {
      const cards = [...actCards].map((c) => ({
        title: txt(c.querySelector("h4")),
        body: txt(c.querySelector("p")),
        bullets: bulletsFrom(c.querySelector("ul")),
      }));
      return [{ type: "cards", title, cards }];
    }

    const arch = body.querySelector(".arch");
    if (arch) {
      const cards = [...arch.querySelectorAll(".arch-box")].map((b) => ({
        title: txt(b.querySelector("h4")),
        body: txt(b.querySelector("p")),
        bullets: [],
      }));
      return [{ type: "cards", title, cards }];
    }

    const items = bulletsFrom(body.querySelector("ul"));
    if (items.length) return [{ type: "bullets", title, items }];

    const text = txt(body);
    if (text) return [{ type: "bullets", title, items: text.split(/(?<=[.!])\s+/).slice(0, 8) }];

    return [{ type: "bullets", title, items: [] }];
  }

  function extractDiagram(diagram) {
    const slides = [];
    const h = diagram.querySelector(":scope > .header");
    if (h) {
      slides.push({
        type: "title",
        title: txt(h.querySelector("h1")),
        subtitle: txt(h.querySelector(".subtitle, p")),
        chips: chipsFrom(h, ".stat-chip, .header-chips span"),
      });
    }

    diagram.querySelectorAll(":scope > .layer").forEach((layer) => {
      slides.push(...extractLayer(layer));
    });

    diagram.querySelectorAll(":scope > .mega-section").forEach((mega) => {
      slides.push(...extractMegaSection(mega));
    });

    const footer = diagram.querySelector(":scope > .footer");
    if (footer) {
      slides.push({ type: "footer", text: txt(footer) });
    }
    return slides;
  }

  function extractPage(page) {
    const slides = [];
    const disclaimer = page.querySelector(":scope > .disclaimer");
    if (disclaimer) {
      slides.push({ type: "note", text: txt(disclaimer) });
    }

    const hero = page.querySelector(":scope > .hero, :scope > .header");
    if (hero) {
      slides.push({
        type: "title",
        title: txt(hero.querySelector("h1")),
        subtitle: txt(hero.querySelector("p, .subtitle")),
        chips: chipsFrom(hero, ".hero-chips span, .header-chips span"),
      });
    }

    page.querySelectorAll(":scope > .block").forEach((block) => {
      slides.push(...extractBlock(block));
    });

    const footer = page.querySelector(":scope > .footer");
    if (footer) slides.push({ type: "footer", text: txt(footer) });
    return slides;
  }

  const cover = document.querySelector(".cover-page");
  const logo = cover?.querySelector(".senao-logo")?.src || document.querySelector(".senao-logo")?.src || "";

  const index = [...document.querySelectorAll(".index-list a")].map((a) => ({
    num: txt(a.querySelector(".num")),
    label: a.textContent.replace(/^\s*\d+\s*/, "").trim(),
  }));

  const sections = [...document.querySelectorAll(".compiled-section")].map((sec) => {
    const label = txt(sec.querySelector(".sheet-header .section-label")) || sec.id;
    const inner = sec.querySelector(".section-inner");
    const diagram = inner?.querySelector(":scope > .diagram-container");
    const page = inner?.querySelector(":scope > .page");
    let slides = [];
    if (diagram) slides = extractDiagram(diagram);
    else if (page) slides = extractPage(page);
    return { id: sec.id, label, slides };
  });

  window.__pptExport = { title: txt(cover?.querySelector("h1")) || "Architecture Plan / Diagrams", logo, index, sections };
})();
