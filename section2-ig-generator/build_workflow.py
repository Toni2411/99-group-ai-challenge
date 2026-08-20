"""Generate the n8n workflow JSON for Section 2."""

import json
import uuid
from pathlib import Path

OUT = Path(r"c:\Users\MUHAMMAD FATHONI\Desktop\Tes 99 Group\99-ai-challenge"
           r"\section2-ig-generator\weekly-ig-generator.workflow.json")

BRAND_VOICE = """You write Instagram captions for Rumah123, Indonesia's largest property marketplace.

BRAND VOICE
- Warm, plain-spoken Bahasa Indonesia. Write the way a knowledgeable friend explains something, not the way a brochure sells something.
- Trustworthy above all. Rumah123 is where people make the largest financial decision of their lives. Never oversell.
- Light and human is good. Cringey hard-sell is not.
- Mixing in common English terms people actually use (KPR, cicilan, DP, cash flow) is natural. Forced slang is not.

HARD RULES - these override everything above
- Never promise or imply a guaranteed return, guaranteed approval, or a specific future price.
- Never state an interest rate, price, or percentage unless it appears verbatim in the source article.
- Never present an opinion from the article as established fact.
- No fear-mongering ("buruan sebelum harga naik lagi!") and no false scarcity.
- If the article does not support a claim, leave the claim out.

STRUCTURE
- Hook: one scroll-stopping line, max 60 characters.
- Body: 3-5 short paragraphs. One idea each. Blank line between them.
- A concrete, useful takeaway the reader can act on this week.
- CTA pointing to Rumah123 that reads as helpful, not desperate.
- 8-12 hashtags: mix broad (#properti #rumah123) and specific to the topic and city.
- 2-5 emoji total, used as punctuation, never as decoration."""

SCORER_PROMPT = (
    "You are the editor deciding whether a property news item is worth an "
    "Instagram post for Rumah123.\n\n"
    "Reject an item when it is: a corporate announcement with no reader "
    "benefit, a thinly rewritten press release, purely political, older than "
    "roughly a week, or so thin that a caption would have to invent the "
    "substance.\n\n"
    "Accept when a real person searching for a home would be better off "
    "knowing this.\n\n"
    "ARTICLE\n"
    "Title: {{ $json.title }}\n"
    "Published: {{ $json.isoDate }}\n"
    "Source: {{ $json.source }}\n"
    "Summary: {{ $json.contentSnippet }}\n\n"
    "Decide, and if you accept it, name the angle and the audience it serves."
)

WRITER_PROMPT = (
    BRAND_VOICE
    + "\n\n---\n\nARTICLE\n"
    "Title: {{ $json.title }}\n"
    "Source: {{ $json.source }} ({{ $json.link }})\n"
    "Summary: {{ $json.contentSnippet }}\n\n"
    "EDITORIAL BRIEF\n"
    "Angle: {{ $json.angle }}\n"
    "Target audience: {{ $json.audience }}\n\n"
    "Write the Instagram post. Ground every factual claim in the article "
    "above; if the article does not support something, do not write it."
)

SCORER_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "angle": {"type": "string",
                  "description": "The reader-benefit angle, one sentence"},
        "audience": {"type": "string",
                     "description": "e.g. first-time buyer, investor, renter"},
        "reason": {"type": "string",
                   "description": "Why accepted or rejected, one sentence"},
    },
    "required": ["relevant", "angle", "audience", "reason"],
}

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "caption": {"type": "string",
                    "description": "Full caption, hook included, ready to post"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "image_prompt": {"type": "string",
                         "description": "Prompt for the accompanying visual"},
        "claims": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every factual claim made, for the compliance gate",
        },
    },
    "required": ["hook", "caption", "hashtags", "image_prompt", "claims"],
}

NORMALISE_CODE = r"""// Normalise three feed shapes into one, drop anything older than a week.
const WINDOW_DAYS = 7;
const cutoff = Date.now() - WINDOW_DAYS * 24 * 60 * 60 * 1000;

return $input.all().flatMap((item) => {
  const d = item.json;
  const link = d.link || d.guid || '';
  if (!link) return [];

  const published = new Date(d.isoDate || d.pubDate || Date.now());
  if (published.getTime() < cutoff) return [];

  // Feeds return HTML fragments of wildly different lengths; the model only
  // needs enough to judge the story, and trimming here keeps token cost flat.
  const snippet = String(d.contentSnippet || d.content || d.description || '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 1200);

  let source = 'unknown';
  try { source = new URL(link).hostname.replace(/^www\./, ''); } catch (e) {}

  const title = String(d.title || '').trim();

  // Cheap topical pre-score before spending an LLM call on the item.
  //
  // Two of the three feeds are general business, so the raw pool is mostly
  // irrelevant - the first run of this workflow sent the editor a laptop
  // promotion. The editor correctly rejected all three items it was given and
  // the run produced nothing, which looked like a broken workflow but was
  // actually a starved one. Ranking by topical signal here means the editor
  // spends its judgement on plausible candidates instead of filtering noise.
  const HAY = (title + ' ' + snippet).toLowerCase();
  const TERMS = [
    ['properti', 3], ['rumah', 3], ['hunian', 3], ['apartemen', 3],
    ['kpr', 4], ['mortgage', 4], ['developer', 2], ['perumahan', 3],
    ['real estate', 3], ['tanah', 2], ['sewa', 2], ['kontrakan', 2],
    ['suku bunga', 2], ['bi rate', 2], ['ihsg', -1], ['gadget', -3],
    ['laptop', -3], ['smartphone', -3], ['otomotif', -3], ['kripto', -2],
  ];
  const topicScore = TERMS.reduce(
    (acc, [term, weight]) => acc + (HAY.includes(term) ? weight : 0), 0);

  return [{
    json: {
      title,
      link,
      source,
      isoDate: published.toISOString(),
      contentSnippet: snippet,
      topicScore,
    },
  }];
});"""

COMPLIANCE_CODE = r"""// Deterministic guardrail in front of the model's own judgement.
//
// The brand rules live in the prompt, but a prompt is a request, not a
// constraint - a model that ignores it fails silently and the post ships. These
// patterns are the claims that would actually get Rumah123 in trouble: promised
// returns, invented rates, and manufactured urgency. Anything caught here is
// routed to human review rather than dropped, because a false positive should
// cost a glance, not a story.

const BANNED = [
  { pattern: /dijamin|garansi|pasti (untung|naik|cuan)|guaranteed/i,
    issue: 'Promises a guaranteed outcome' },
  { pattern: /pasti disetujui|approval dijamin/i,
    issue: 'Promises loan approval' },
  { pattern: /buruan sebelum|jangan sampai kehabisan|terakhir hari ini/i,
    issue: 'Manufactured urgency or false scarcity' },
  { pattern: /\b\d+(?:[.,]\d+)?\s*%/,
    issue: 'States a percentage - must appear verbatim in the source' },
];

// The editorial brief has to be fetched from upstream by name. Write Caption
// replaces each item's json with the model's structured output, so the title,
// link and angle carried this far are gone by the time this node runs - they
// rendered as "undefined" in the first digest, and the carve-out below silently
// compared every percentage against an empty string.
const briefs = $('Carry Brief Forward').all();

return $input.all().map((item, i) => {
  const post = item.json.output ?? item.json;
  const brief = (briefs[i] || {}).json || {};
  const caption = String(post.caption || '');
  const article = String(brief.contentSnippet || '');

  const issues = [];
  for (const { pattern, issue } of BANNED) {
    const hit = caption.match(pattern);
    if (!hit) continue;
    // A percentage quoted straight from the article is legitimate.
    if (issue.startsWith('States a percentage') && article.includes(hit[0])) continue;
    issues.push(`${issue} -> "${hit[0]}"`);
  }

  const hashtags = Array.isArray(post.hashtags) ? post.hashtags : [];
  if (hashtags.length < 8) issues.push(`Only ${hashtags.length} hashtags, want 8-12`);
  if (caption.length > 2200) issues.push('Caption exceeds the Instagram limit');

  return {
    json: {
      ...post,
      hashtags,
      sourceTitle: brief.title,
      sourceLink: brief.link,
      angle: brief.angle,
      audience: brief.audience,
      issues,
      needsReview: issues.length > 0,
      generatedAt: new Date().toISOString(),
    },
  };
});"""

DIGEST_CODE = r"""// Collapse the run into one markdown digest a human can read in 30 seconds.
const posts = $input.all().map((i) => i.json);
const stamp = new Date().toISOString().slice(0, 10);

const approved = posts.filter((p) => !p.needsReview);
const flagged = posts.filter((p) => p.needsReview);

const render = (p) => [
  `### ${p.hook}`,
  '',
  `**Source:** [${p.sourceTitle}](${p.sourceLink})  `,
  `**Angle:** ${p.angle}  `,
  `**Audience:** ${p.audience}`,
  '',
  p.issues.length ? `> **Held for review:** ${p.issues.join('; ')}\n` : '',
  '```',
  p.caption,
  '',
  (p.hashtags || []).join(' '),
  '```',
  '',
  `*Image prompt:* ${p.image_prompt}`,
  '',
  '---',
  '',
].join('\n');

const md = [
  `# Rumah123 weekly Instagram digest - ${stamp}`,
  '',
  `${approved.length} ready to post, ${flagged.length} held for review.`,
  '',
  approved.length ? '## Ready to post\n' : '',
  ...approved.map(render),
  flagged.length ? '## Held for review\n' : '',
  ...flagged.map(render),
].join('\n');

// md must live inside json - an n8n item only carries `json` and
// `binary`, so a sibling field is silently dropped and the downstream
// Convert to File node finds nothing.
return [{ json: { filename: `digest-${stamp}.md`, md, posts } }];"""


NS = uuid.UUID("3f2b1a44-99c0-4a1e-9c3d-0b5e7a1d2c88")


def node(name, type_, version, pos, params=None, creds=None):
    # n8n expects UUID node ids; deriving them from the name keeps them stable
    # across regenerations so the diff stays readable.
    n = {
        "parameters": params or {},
        "id": str(uuid.uuid5(NS, name)),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": pos,
    }
    if creds:
        n["credentials"] = creds
    return n


GEMINI_CRED = {"googlePalmApi": {"id": "yD6JfqYTBTrWBAeh", "name": "Google Gemini (AI Studio)"}}

nodes = [
    node("Weekly Monday 08:00", "n8n-nodes-base.scheduleTrigger", 1.2, [0, 400], {
        "rule": {"interval": [{"field": "weeks", "triggerAtDay": [1],
                               "triggerAtHour": 8, "triggerAtMinute": 0}]}
    }),
    node("Tempo Bisnis", "n8n-nodes-base.rssFeedRead", 1.1, [240, 200],
         {"url": "https://rss.tempo.co/bisnis", "options": {}}),
    node("Detik Properti", "n8n-nodes-base.rssFeedRead", 1.1, [240, 400],
         {"url": "https://finance.detik.com/properti/rss", "options": {}}),
    node("CNBC Market", "n8n-nodes-base.rssFeedRead", 1.1, [240, 600],
         {"url": "https://www.cnbcindonesia.com/market/rss", "options": {}}),
    node("Merge Feeds", "n8n-nodes-base.merge", 3, [480, 400],
         {"numberInputs": 3}),
    node("Normalise & Window", "n8n-nodes-base.code", 2, [700, 400],
         {"jsCode": NORMALISE_CODE}),
    node("Drop Seen Stories", "n8n-nodes-base.removeDuplicates", 2, [920, 400], {
        "operation": "removeItemsSeenInPreviousExecutions",
        "logic": "removeItemsWithAlreadySeenKeyValues",
        "dedupeValue": "={{ $json.link }}",
        "options": {},
    }),
    node("Rank By Topic", "n8n-nodes-base.sort", 1, [1140, 400], {
        "sortFieldsUi": {"sortField": [
            {"fieldName": "topicScore", "order": "descending"}]},
        "options": {},
    }),
    node("Top 8 Candidates", "n8n-nodes-base.limit", 1, [1250, 400],
         {"maxItems": 8}),
    node("Score Newsworthiness", "@n8n/n8n-nodes-langchain.chainLlm", 1.5,
         [1360, 400], {"promptType": "define", "text": f"={SCORER_PROMPT}",
                       "hasOutputParser": True}),
    node("Gemini - Editor", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1,
         [1300, 620], {"modelName": "models/gemini-3.5-flash-lite",
                       "options": {"temperature": 0.2}}, GEMINI_CRED),
    node("Editor Schema", "@n8n/n8n-nodes-langchain.outputParserStructured",
         1.2, [1480, 620], {"schemaType": "manual",
                            "inputSchema": json.dumps(SCORER_SCHEMA, indent=2)}),
    node("Worth Posting?", "n8n-nodes-base.if", 2.2, [1620, 400], {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "",
                        "typeValidation": "loose", "version": 2},
            "conditions": [{
                "id": "relevant",
                "leftValue": "={{ $json.output.relevant }}",
                "rightValue": True,
                "operator": {"type": "boolean", "operation": "true",
                             "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    }),
    node("Carry Brief Forward", "n8n-nodes-base.set", 3.4, [1840, 300], {
        "mode": "manual",
        "assignments": {"assignments": [
            {"id": "a1", "name": "title", "type": "string",
             "value": "={{ $('Top 8 Candidates').item.json.title }}"},
            {"id": "a2", "name": "link", "type": "string",
             "value": "={{ $('Top 8 Candidates').item.json.link }}"},
            {"id": "a3", "name": "source", "type": "string",
             "value": "={{ $('Top 8 Candidates').item.json.source }}"},
            {"id": "a4", "name": "contentSnippet", "type": "string",
             "value": "={{ $('Top 8 Candidates').item.json.contentSnippet }}"},
            {"id": "a5", "name": "angle", "type": "string",
             "value": "={{ $json.output.angle }}"},
            {"id": "a6", "name": "audience", "type": "string",
             "value": "={{ $json.output.audience }}"},
        ]},
        "options": {},
    }),
    node("Write Caption", "@n8n/n8n-nodes-langchain.chainLlm", 1.5,
         [2060, 300], {"promptType": "define", "text": f"={WRITER_PROMPT}",
                       "hasOutputParser": True}),
    node("Gemini - Copywriter", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
         1, [2000, 520], {"modelName": "models/gemini-3.5-flash-lite",
                          "options": {"temperature": 0.8}}, GEMINI_CRED),
    node("Caption Schema", "@n8n/n8n-nodes-langchain.outputParserStructured",
         1.2, [2180, 520], {"schemaType": "manual",
                            "inputSchema": json.dumps(WRITER_SCHEMA, indent=2)}),
    node("Brand Safety Gate", "n8n-nodes-base.code", 2, [2300, 300],
         {"jsCode": COMPLIANCE_CODE}),
    node("Build Digest", "n8n-nodes-base.code", 2, [2520, 300],
         {"mode": "runOnceForAllItems", "jsCode": DIGEST_CODE}),
    node("Digest to File", "n8n-nodes-base.convertToFile", 1.1, [2740, 300], {
        "operation": "toText",
        "sourceProperty": "md",
        "options": {"fileName": "={{ $json.filename }}"},
    }),
    node("Save Digest", "n8n-nodes-base.readWriteFile", 1, [2960, 300], {
        "operation": "write",
        # Derive the name from the clock, not from $json. Convert to File
        # replaces the item's json with binary data, so any field carried from
        # Build Digest is gone by the time this node reads it - the path
        # collapsed to the bare directory and n8n rejected it.
        "fileName": "=./output/digest-{{ $now.toFormat('yyyy-MM-dd') }}.md",
        "options": {},
    }),
    node("No Story This Week", "n8n-nodes-base.noOp", 1, [1840, 520]),
    # Lets the workflow be run on demand and called as a sub-workflow, rather
    # than only firing on Monday. Also what `n8n execute --id` starts from.
    node("Run On Demand", "n8n-nodes-base.executeWorkflowTrigger", 1, [0, 620],
         {"inputSource": "passthrough"}),
]

connections = {
    "Weekly Monday 08:00": {"main": [[
        {"node": "Tempo Bisnis", "type": "main", "index": 0},
        {"node": "Detik Properti", "type": "main", "index": 0},
        {"node": "CNBC Market", "type": "main", "index": 0},
    ]]},
    "Run On Demand": {"main": [[
        {"node": "Tempo Bisnis", "type": "main", "index": 0},
        {"node": "Detik Properti", "type": "main", "index": 0},
        {"node": "CNBC Market", "type": "main", "index": 0},
    ]]},
    "Tempo Bisnis": {"main": [[{"node": "Merge Feeds", "type": "main", "index": 0}]]},
    "Detik Properti": {"main": [[{"node": "Merge Feeds", "type": "main", "index": 1}]]},
    "CNBC Market": {"main": [[{"node": "Merge Feeds", "type": "main", "index": 2}]]},
    "Merge Feeds": {"main": [[{"node": "Normalise & Window", "type": "main", "index": 0}]]},
    "Normalise & Window": {"main": [[{"node": "Drop Seen Stories", "type": "main", "index": 0}]]},
    "Drop Seen Stories": {"main": [[{"node": "Rank By Topic", "type": "main", "index": 0}]]},
    "Rank By Topic": {"main": [[{"node": "Top 8 Candidates", "type": "main", "index": 0}]]},
    "Top 8 Candidates": {"main": [[{"node": "Score Newsworthiness", "type": "main", "index": 0}]]},
    "Gemini - Editor": {"ai_languageModel": [[
        {"node": "Score Newsworthiness", "type": "ai_languageModel", "index": 0}]]},
    "Editor Schema": {"ai_outputParser": [[
        {"node": "Score Newsworthiness", "type": "ai_outputParser", "index": 0}]]},
    "Score Newsworthiness": {"main": [[{"node": "Worth Posting?", "type": "main", "index": 0}]]},
    "Worth Posting?": {"main": [
        [{"node": "Carry Brief Forward", "type": "main", "index": 0}],
        [{"node": "No Story This Week", "type": "main", "index": 0}],
    ]},
    "Carry Brief Forward": {"main": [[{"node": "Write Caption", "type": "main", "index": 0}]]},
    "Gemini - Copywriter": {"ai_languageModel": [[
        {"node": "Write Caption", "type": "ai_languageModel", "index": 0}]]},
    "Caption Schema": {"ai_outputParser": [[
        {"node": "Write Caption", "type": "ai_outputParser", "index": 0}]]},
    "Write Caption": {"main": [[{"node": "Brand Safety Gate", "type": "main", "index": 0}]]},
    "Brand Safety Gate": {"main": [[{"node": "Build Digest", "type": "main", "index": 0}]]},
    "Build Digest": {"main": [[{"node": "Digest to File", "type": "main", "index": 0}]]},
    "Digest to File": {"main": [[{"node": "Save Digest", "type": "main", "index": 0}]]},
}

workflow = {
    "id": str(uuid.uuid5(NS, "workflow"))[:16].replace("-", ""),
    "name": "Rumah123 Weekly IG Post Generator",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "timezone": "Asia/Jakarta"},
    "active": False,
    "versionId": str(uuid.uuid5(NS, "v1")),
    "pinData": {},
    "meta": {"instanceId": "99-group-ai-challenge-section-2"},
    "tags": [],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")

# Validate structure: every connection target must exist.
names = {n["name"] for n in nodes}
missing = []
for src, kinds in connections.items():
    if src not in names:
        missing.append(f"source {src}")
    for outs in kinds.values():
        for group in outs:
            for c in group:
                if c["node"] not in names:
                    missing.append(f"target {c['node']}")

print(f"nodes: {len(nodes)}  connections: {len(connections)}")
print("dangling:", missing or "none")
print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
json.loads(OUT.read_text(encoding="utf-8"))
print("JSON valid")
