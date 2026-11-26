// Demo app with three flags in different lifecycle states.
// new_landing_page: enabled everywhere, fully rolled out -> stale (bake-in candidate)
// old_checkout: disabled everywhere, dead code path -> stale (removal candidate)
// experimental_search: enabled in Development only -> active, must be left alone

const ENVIRONMENT_ID = "BbmawJqFms5tzTyiRMboJw"; // Flagsmith Development env key (demo)

flagsmith.init({
  environmentID: ENVIRONMENT_ID,
  onChange: () => render(),
});

function render() {
  // --- stale flag 1: fully rolled out, branch never changes anymore
  if (flagsmith.hasFeature("new_landing_page")) {
    document.getElementById("landing").classList.add("landing-v2");
    document.getElementById("landing").innerText = "Landing page v2";
  }

  // --- stale flag 2: disabled everywhere, this path never runs
  if (flagsmith.hasFeature("old_checkout")) {
    document.getElementById("checkout").innerText = "New checkout experience";
  }

  // --- active flag: still being tested, janitor must skip it
  if (flagsmith.hasFeature("experimental_search")) {
    document.getElementById("search").innerHTML =
      '<input placeholder="Experimental search...">';
  }
}
