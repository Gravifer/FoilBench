import {mount} from "svelte";

import App from "./App.svelte";
import "./theme.css";

const target = document.querySelector<HTMLDivElement>("#app");
if (target === null) throw new Error("FoilBench application root is missing");

mount(App, {target});
