//! Builds the TyVcd state the way surfer-tywaves does, rewrites the VCD,
//! and prints one line per rewritten variable in header order.
use std::path::Path;
use tywaves_rs::hgldd;
use tywaves_rs::tyvcd::builder::{BuilderError, GenericBuilder, TyVcdBuilder};
use tywaves_rs::tyvcd::spec::{ScopeDef, Variable, VariableKind};
use tywaves_rs::tyvcd::trace_pointer::TraceGetter;
use tywaves_rs::vcd_rewrite::VcdRewriter;

fn kind(v: &Variable) -> String {
    let fields = |fs: &Vec<Variable>| {
        fs.iter()
            .map(|f| format!("{}:{}:{}", f.name, f.high_level_info.type_name, kind(f)))
            .collect::<Vec<_>>()
            .join(",")
    };
    match &v.kind {
        VariableKind::Ground(w) => format!("g{}", w),
        VariableKind::Struct { fields: fs } => format!("s{{{}}}", fields(fs)),
        VariableKind::Vector { fields: fs } => format!("v[{}]", fields(fs)),
        VariableKind::External => "ext".into(),
    }
}

fn leaves(v: &Variable, path: &str, out: &mut Vec<String>) {
    match &v.kind {
        VariableKind::Struct { fields } | VariableKind::Vector { fields } => {
            for f in fields {
                leaves(f, &format!("{}.{}", path, f.name), out);
            }
        }
        VariableKind::Ground(w) => out.push(format!("{}\t{}\t{:?}", path, w, v.get_trace_value())),
        VariableKind::External => out.push(format!("{}\text\t{:?}", path, v.get_trace_value())),
    }
}

fn walk(s: &ScopeDef, path: &[String], out: &mut Vec<String>, lv: &mut Vec<String>) {
    let name = s.get_trace_name().cloned().unwrap_or_else(|| "?".into());
    let p = [path, &[name]].concat();
    for v in &s.variables {
        leaves(v, &format!("{}\t{}", p.join("."), v.name), lv);
        let mut e: Vec<(i64, String)> = v
            .enum_val_map
            .as_ref()
            .map(|m| m.read().unwrap().iter().map(|(k, x)| (*k, x.clone())).collect())
            .unwrap_or_default();
        e.sort();
        let params: Vec<String> = v
            .high_level_info
            .params
            .iter()
            .map(|q| format!("{}:{}={:?}", q.name, q.tpe, q.value))
            .collect();
        out.push(format!(
            "{}\t{}\t{}\t{}\t{}\t{:?}\t{:?}",
            p.join("."),
            v.name,
            v.high_level_info.type_name,
            params.join(","),
            kind(v),
            e,
            v.get_trace_value()
        ));
    }
    for c in s.subscopes.values() {
        walk(&c.read().unwrap(), &p, out, lv);
    }
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (dir, vcd, top, extra, out) = (&a[1], &a[2], &a[3], &a[4], &a[5]);
    let hg = hgldd::reader::parse_hgldd_dir(Path::new(dir)).expect("parse hgldd");
    let extra: Vec<String> = extra.split(',').filter(|s| !s.is_empty()).map(String::from).collect();
    let mut b = TyVcdBuilder::init(hg).with_extra_artifact_scopes(extra, top);
    match b.build() {
        Ok(_) => {}
        Err(BuilderError::MissingTraceValue(e)) => eprintln!("MissingTraceValue: {:?}", e),
        Err(e) => panic!("build: {:?}", e),
    }
    let ty = b.get_copy().expect("tyvcd");
    let scopes: Vec<ScopeDef> = ty.scopes.values().map(|v| v.read().unwrap().clone()).collect();
    let (mut lines, mut lv) = (vec![], vec![]);
    for s in &scopes {
        walk(s, &[], &mut lines, &mut lv);
    }
    std::fs::write(format!("{}.leaves.tsv", out), lv.join("\n") + "\n").unwrap();
    std::fs::write(format!("{}.vars.tsv", out), lines.join("\n") + "\n").unwrap();
    let mut rw = VcdRewriter::new(Path::new(vcd), scopes, format!("{}.vcd", out)).expect("rewriter");
    rw.rewrite().expect("rewrite");
}
