# Migrating to Zensical

With the currently implementation of Zensical build times are very long, even when only specifying one item in the navigation. They also do not allow setting `docs_dir` to ".". This would be a big break in our docs are require a decent amount of updates for links and other references.

There is also no support for versioning docs but it is on the roadmap. Please see the [plugins compatibility](https://zensical.org/compatibility/plugins/) page on the Zensical site.

## Missing Extensions

The following extensions are not listed on the Zensical site as supported. We could investigate if they are still required within our documentation.

- `mdx_truly_sane_lists`
- `pymdownx.critic`
- `pymdownx.magiclink`
- `smarty`

## Plugins

- `exclude` ([not maintained](https://github.com/apenwarr/mkdocs-exclude), looking at git history)
- `include_dir_to_nav` ([not maintained](https://github.com/mysiki/mkdocs_include_dir_to_nav))
- `git-revision-date-localized` (maintained, unsure of Zensical support)
- `mkdocstrings` ([some support](https://zensical.org/docs/setup/extensions/mkdocstrings/?h=mkdo) in Zensical)
- `same-dir` (maintained but it looks like Zensical also does not support using "." as docs_dir)

## Unsupported settings

- Cannot  set docs_dir to "."
- `exclude_docs`
- `not_in_nav`
- `strict`
