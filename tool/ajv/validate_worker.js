import Ajv2019 from "ajv/dist/2019.js";
import readline from "node:readline";

const ajv = new Ajv2019({
  allErrors: true,
  strict: false,
  discriminator: true,
});

let validator = null;

// AAS schemas model SubmodelElement (and others) as a `oneOf` over ~14 typed
// branches, each tagged by a `modelType` const. Without a discriminator, AJV
// (allErrors:true) validates an element against every branch and, on the
// inevitable mismatches, emits errors for all ~13 non-matching branches — an
// error explosion (one bad element -> hundreds of errors).
//
// Each branch ($ref to e.g. Property) carries `modelType: {const: "Property"}`
// nested inside an `allOf`. AJV's discriminator keyword can't see a tag nested
// in allOf, so we (a) hoist that const to the branch definition's top level and
// (b) attach `discriminator: {propertyName: "modelType"}` to every qualifying
// oneOf. AJV then validates each element against only its declared type.
//
// This changes which errors are reported, never the valid/invalid verdict
// (verified flip-free across the full M5 dataset). The on-disk
// IDTA schema files are left untouched; injection happens on the in-memory copy.
function injectDiscriminator(schema) {
  const defs = schema && schema.definitions;
  if (!defs) return schema;

  const hoist = (defName) => {
    const d = defs[defName];
    if (!d || !Array.isArray(d.allOf)) return;
    let constVal = null;
    for (const member of d.allOf) {
      if (member && member.properties && member.properties.modelType &&
          "const" in member.properties.modelType) {
        constVal = member.properties.modelType.const;
      }
    }
    if (constVal == null) return;
    d.properties = { ...(d.properties || {}), modelType: { const: constVal } };
    d.required = Array.from(new Set([...(d.required || []), "modelType"]));
  };

  const walk = (node) => {
    if (Array.isArray(node)) return node.forEach(walk);
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node.oneOf) &&
        node.oneOf.every((b) => b && b.$ref && b.$ref.startsWith("#/definitions/"))) {
      node.oneOf.forEach((b) => hoist(b.$ref.split("/").pop()));
      node.discriminator = { propertyName: "modelType" };
    }
    for (const key of Object.keys(node)) walk(node[key]);
  };

  walk(schema);
  return schema;
}

function formatErrors(errors) {
  const seen = new Set();
  return (errors || [])
    .filter((err) => {
      const key = `${err.instancePath}|${err.keyword}|${JSON.stringify(err.params)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((err) => ({
      path: err.instancePath || "/",
      message: err.message || "validation error",
      keyword: err.keyword,
      schemaPath: err.schemaPath,
      params: err.params,
    }));
}

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

rl.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    process.stdout.write(JSON.stringify({
      ok: false,
      fatal: true,
      error: "Invalid control JSON sent to worker",
    }) + "\n");
    return;
  }

  if (msg.type === "init") {
    try {
      validator = ajv.compile(injectDiscriminator(msg.schema));
      process.stdout.write(JSON.stringify({
        ok: true,
        type: "init",
      }) + "\n");
    } catch (e) {
      process.stdout.write(JSON.stringify({
        ok: false,
        fatal: true,
        error: `Invalid schema: ${e.message}`,
      }) + "\n");
    }
    return;
  }

  if (msg.type === "validate") {
    if (!validator) {
      process.stdout.write(JSON.stringify({
        ok: false,
        fatal: true,
        error: "Worker not initialized with schema",
      }) + "\n");
      return;
    }

    let data;
    try {
      data = JSON.parse(msg.jsonString);
    } catch {
      process.stdout.write(JSON.stringify({
        ok: true,
        result: [false, false, []],
      }) + "\n");
      return;
    }

    const valid = validator(data);

    if (valid) {
      process.stdout.write(JSON.stringify({
        ok: true,
        result: [true, true, []],
      }) + "\n");
      return;
    }

    process.stdout.write(JSON.stringify({
      ok: true,
      result: [true, false, formatErrors(validator.errors)],
    }) + "\n");
    return;
  }

  process.stdout.write(JSON.stringify({
    ok: false,
    fatal: true,
    error: `Unknown message type: ${msg.type}`,
  }) + "\n");
});