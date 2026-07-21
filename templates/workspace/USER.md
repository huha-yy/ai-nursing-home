# User Profile

**Name:** {{ user.name | default('User') }}
**Pronouns:** {{ user.pronouns | default('they/them') }}
**Timezone:** {{ user.timezone | default('UTC') }}

## Context

{{ user.context | default('No additional context provided.') }}
