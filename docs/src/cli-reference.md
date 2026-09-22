---
layout: layouts/base.njk
title: CLI reference
description: Complete wifimap command and option reference, including defaults, required values, choices, and descriptions.
navKey: cli-reference
permalink: /cli-reference/index.html
---

# CLI reference

Global options come before the subcommand. For example, use
`wifimap --db other.db eval`, not `wifimap eval --db other.db`. Location,
room, and spot arguments accept an ID or a name unless an option says
otherwise.

<section class="reference-block generated-reference" aria-labelledby="command-index">
  <h2 id="command-index">Command index</h2>
  <ul class="command-index">
  {% for command in capabilities.cli.commands %}
    <li>
      <a href="#command-{{ loop.index }}"><code>{{ capabilities.cli.program }}{% if command.path | length %} {{ command.path | join(" ") }}{% endif %}</code></a>
      {% if command.summary %}<span> — {{ command.summary }}</span>{% endif %}
    </li>
  {% endfor %}
  </ul>
</section>

{% for command in capabilities.cli.commands %}
<section class="reference-block generated-reference command-reference" aria-labelledby="command-{{ loop.index }}">
  <h2 id="command-{{ loop.index }}"><code>{{ capabilities.cli.program }}{% if command.path | length %} {{ command.path | join(" ") }}{% endif %}</code></h2>
  {% if command.summary %}<p>{{ command.summary }}</p>{% endif %}

  <h3>Options</h3>
  {% if command.options | length %}
  <div class="option-list">
  {% for option in command.options %}
    <article class="option-reference">
      <h4><code>{% if option.flags | length %}{{ option.flags | join(", ") }}{% else %}{{ option.destination }}{% endif %}</code></h4>
      <dl class="option-metadata">
        <div>
          <dt>Description</dt>
          <dd>{% if option.help %}{{ option.help }}{% else %}No description available.{% endif %}</dd>
        </div>
        <div>
          <dt>Required</dt>
          <dd>{% if option.required %}Yes{% else %}No{% endif %}</dd>
        </div>
        <div>
          <dt>Default</dt>
          <dd><code>{% if option.default == null %}None{% elif option.default == true %}true{% elif option.default == false %}false{% else %}{{ option.default }}{% endif %}</code></dd>
        </div>
        <div>
          <dt>Choices</dt>
          <dd>
          {% if option.choices == null %}
            Any accepted value
          {% elif option.choices | length %}
            {% for choice in option.choices %}<code>{{ choice }}</code>{% if not loop.last %}, {% endif %}{% endfor %}
          {% else %}
            No choices
          {% endif %}
          </dd>
        </div>
        <div>
          <dt>Input</dt>
          <dd>{% if option.kind == "flag" %}Flag{% else %}Value{% if option.metavar %} <code>{{ option.metavar }}</code>{% endif %}{% endif %}</dd>
        </div>
      </dl>
    </article>
  {% endfor %}
  </div>
  {% else %}
  <p>This command has no options.</p>
  {% endif %}
</section>
{% endfor %}

## Exit behavior

Commands use the following exit codes:

<div class="reference-block generated-reference">
  <p>{{ capabilities.cli.epilog }}</p>
</div>

[Exit-code recovery](/data-troubleshooting/)
