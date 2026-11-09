// Inicio module descriptor (owner P08; module-registry.json › modules[inicio].descriptor_file).
// Inicio composes KPIs registered by provider modules at runtime; providers not yet accepted render the honest
// state "Módulo aún no disponible" (never invented numbers). No page overrides: the shell dashboard renders it.
import type { ModuleDescriptor } from "../registry";

const inicio: ModuleDescriptor = {
  id: "inicio",
};

export default inicio;
