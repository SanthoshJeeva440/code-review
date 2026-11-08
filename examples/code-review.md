<!-- Optional: copy to .github/code-review.md in your repository to steer the reviewer.
     It is read from the base branch, so a PR cannot change its own review rules. -->
# Review guidelines

- This is a Java 21 / Spring Boot service. Flag blocking calls inside reactive (WebFlux) code.
- All SQL must go through the repository layer; flag string-built queries.
- Public APIs need Javadoc. Do not comment on formatting; Spotless handles it.
- Treat anything under `legacy/` as frozen: only flag security issues there.
