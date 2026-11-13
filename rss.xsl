<?xml version="1.0" encoding="UTF-8"?>
<!-- Renders /rss.xml as a readable page instead of raw XML.
     Feed readers ignore this processing instruction and read the feed normally. -->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:atom="http://www.w3.org/2005/Atom">
<xsl:output method="html" encoding="UTF-8" indent="yes"/>
<xsl:template match="/">
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>RadarJobs — RSS feed</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&amp;family=IBM+Plex+Mono:wght@400;500;600&amp;display=swap" rel="stylesheet"/>
<style>
  html { color-scheme: dark; }
  body { margin:0; background:#121317; color:#e3e2e8; font:400 14px/1.5 'IBM Plex Sans', system-ui, sans-serif; }
  a { color:#ddb7ff; text-decoration:none; }
  a:hover { text-decoration:underline; }
  header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:16px 24px; border-bottom:1px solid rgba(77,67,84,.4); }
  .brand { font:600 15px/1 'IBM Plex Mono', monospace; letter-spacing:.08em; text-transform:uppercase; color:#e3e2e8; }
  main { max-width:820px; margin:0 auto; padding:40px 24px 64px; }
  h1 { margin:0 0 8px; font:600 28px/1.2 'IBM Plex Sans', system-ui, sans-serif; letter-spacing:-.02em; }
  .sub { margin:0 0 4px; color:#cfc2d6; }
  .meta { margin:0 0 28px; color:#988d9f; font:500 12px/1.5 'IBM Plex Mono', monospace; }
  ul { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
  li { padding:14px 16px; border-radius:12px; background:#1a1b20; }
  li a { font:600 15px/1.3 'IBM Plex Sans', system-ui, sans-serif; }
  .d { display:block; margin-top:4px; color:#cfc2d6; font-size:13px; }
  .p { display:block; margin-top:6px; color:#988d9f; font:500 11px/1 'IBM Plex Mono', monospace; letter-spacing:.06em; }
  footer { max-width:820px; margin:0 auto; padding:0 24px 48px; color:#988d9f; font-size:12px; }
</style>
</head>
<body>
<header>
  <a class="brand" href="/">RadarJobs</a>
  <a href="/jobs">Open the job board</a>
</header>
<main>
  <h1><xsl:value-of select="rss/channel/title"/></h1>
  <p class="sub"><xsl:value-of select="rss/channel/description"/></p>
  <p class="meta">
    Last built: <xsl:value-of select="rss/channel/lastBuildDate"/>
    <xsl:text> · </xsl:text>
    <xsl:value-of select="count(rss/channel/item)"/> items
    <xsl:text> · subscribe with this URL: </xsl:text>
    <xsl:value-of select="rss/channel/atom:link/@href"/>
  </p>
  <ul>
    <xsl:for-each select="rss/channel/item">
      <li>
        <a href="{link}" target="_blank" rel="noopener noreferrer"><xsl:value-of select="title"/></a>
        <span class="d"><xsl:value-of select="description"/></span>
        <span class="p"><xsl:value-of select="pubDate"/></span>
      </li>
    </xsl:for-each>
  </ul>
</main>
<footer>This page is an XML feed rendered for humans. Add <strong>/rss.xml</strong> to any RSS reader to subscribe.</footer>
</body>
</html>
</xsl:template>
</xsl:stylesheet>
