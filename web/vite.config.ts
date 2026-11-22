/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

/** The kit's stylesheets keep fixed names and are cached for an hour, so each page asks for them with the kit's own
 * hash (public/family/VERSION) in the address: after a kit update, a new page never gets an old stylesheet. */
const kitHash = readFileSync(here("./public/family/VERSION"), "utf8").match(/sha256 ([0-9a-f]{12})/)?.[1] ?? "";
const versionKit = (html: string) =>
  kitHash ? html.replace(/(href=")(\/family\/[\w-]+\.css)(")/g, `$1$2?v=${kitHash}$3`) : html;

/** Static pages share one header and footer: <!--#include header--> pulls in partials/header.html, and
 * <!--#include family/shelf--> the Where They Stand family kit's public/family/shelf.html (copied in by the kit's
 * sync.mjs; see docs/DESIGN.md, "The family kit"). */
function partials(): Plugin {
  return {
    name: "html-partials",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => {
        // partials may include other partials (the header includes the seal)
        for (let depth = 0; depth < 5 && html.includes("<!--#include "); depth++) {
          html = html.replace(/<!--#include (family\/)?([\w-]+)-->/g, (_m, family: string | undefined, name: string) =>
            readFileSync(here(family ? `./public/family/${name}.html` : `./partials/${name}.html`), "utf8"),
          );
        }
        html = versionKit(html);
        // each page's link preview carries its own title and description
        const title = html.match(/<title>([^<]*)<\/title>/)?.[1];
        const description = html.match(/<meta name="description" content="([^"]*)"/)?.[1];
        if (title) html = html.replace(/(<meta property="og:title" content=")[^"]*(")/, `$1${title}$2`);
        if (description) html = html.replace(/(<meta property="og:description" content=")[^"]*(")/, `$1${description}$2`);
        return html;
      },
    },
  };
}

export default defineConfig({
  plugins: [partials(), react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rolldownOptions: {
      input: {
        main: here("./index.html"),
        methodology: here("./methodology.html"),
        status: here("./status.html"),
        corrections: here("./corrections.html"),
        named: here("./named.html"),
        accessibility: here("./accessibility.html"),
        resources: here("./resources.html"),
        glossary: here("./glossary.html"),
        about: here("./about.html"),
        privacy: here("./privacy.html"),
        // the privacy notice while measurement is on, served at /privacy then only (hansard/api/measure.py)
        privacyMeasurement: here("./privacy-measurement.html"),
        terms: here("./terms.html"),
        person: here("./person.html"),
        people: here("./people.html"),
        topics: here("./topics.html"),
        timeline: here("./timeline.html"),
        bills: here("./bills.html"),
        bill: here("./bill.html"),
        debate: here("./debate.html"),
        week: here("./week.html"),
        edms: here("./edms.html"),
        edm: here("./edm.html"),
        committees: here("./committees.html"),
        committee: here("./committee.html"),
        inquiry: here("./inquiry.html"),
        witnesses: here("./witnesses.html"),
        donations: here("./donations.html"),
        directorships: here("./directorships.html"),
        groups: here("./groups.html"),
        group: here("./group.html"),
        devolved: here("./devolved.html"),
        devolvedday: here("./devolvedday.html"),
        developers: here("./developers.html"),
        press: here("./press.html"),
        evidence: here("./evidence.html"),
        charts: here("./charts.html"),
        lighter: here("./lighter-side.html"),
        compare: here("./compare.html"),
        notfound: here("./404.html"),
        admin: here("./admin.html"),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
