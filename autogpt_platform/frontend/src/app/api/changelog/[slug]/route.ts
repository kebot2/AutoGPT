import { NextResponse } from "next/server";
import { CHANGELOG_MANIFEST } from "@/components/changelog/manifest";

const DOCS_BASE = "https://agpt.co/docs/platform/changelog/changelog";
const STALE_WHILE_REVALIDATE = 60 * 60 * 24;

export const revalidate = 3600; // 1 hour — must be a static literal for Next.js segment config

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;

  if (!CHANGELOG_MANIFEST.some((e) => e.slug === slug)) {
    return new NextResponse("Unknown changelog entry", { status: 404 });
  }

  const upstream = `${DOCS_BASE}/${slug}.md`;

  try {
    const res = await fetch(upstream, {
      next: { revalidate: 3600 },
      headers: { Accept: "text/markdown, text/plain, */*" },
    });

    if (!res.ok) {
      return new NextResponse(`Upstream returned ${res.status}`, {
        status: 502,
      });
    }

    const text = await res.text();

    return new NextResponse(text, {
      status: 200,
      headers: {
        "Content-Type": "text/markdown; charset=utf-8",
        "Cache-Control": `public, s-maxage=3600, stale-while-revalidate=${STALE_WHILE_REVALIDATE}`,
      },
    });
  } catch (err) {
    return new NextResponse(
      `Failed to fetch changelog: ${err instanceof Error ? err.message : "unknown error"}`,
      { status: 502 },
    );
  }
}
