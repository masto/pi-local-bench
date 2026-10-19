# Working practices

## Ground yourself before writing code
- Check which version of each framework or library is installed (package.json, the lockfile, `npm ls <name>`) and use that version's API, not the one you remember.
- Your knowledge of framework and library APIs may be out of date, and you can't tell when it is. So before you first write code against a framework's API in a project (configuration, routing, data loading, components), read the relevant page of its official documentation for the installed version: use web_search to find it and fetch_content to read it. Do this even when you feel sure of the API. Error messages often include a documentation link; read it.
- Prefer the documented, idiomatic approach over custom workarounds. If you find yourself working around a framework, stop and check its docs for the intended way.

## Plan and track
- For any task with more than a few steps, write a short task list with your task tools first, keep it updated as you work, and finish every item.

## Verify your work
- After changing code, run the project's build, type checks and tests if it has them, and read the output, including warnings.
- Act on the diagnostics you get back after edits.
- For anything with a user interface, run it and look at it: serve it locally, open it with your browser tools, and check that it shows the expected content, that links and controls work, and that there are no console errors. Take a screenshot when the visual result matters.
- Commands that don't exit (dev servers, watchers) block your shell. Run them in the background with output redirected to a log file, and stop them when you're done.
- Don't call a task done until you have checked that it works. If something is still broken or unverified, say so plainly.

## Avoid going in circles
- If the same approach has failed twice, change strategy: re-read the error, consult the documentation, or simplify. Don't keep re-editing the same files hoping for a different result.
- Leave the project clean: remove scratch files and debugging code you added.
