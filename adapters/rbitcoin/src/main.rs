//! Conformance protocol v1 interface to rbitcoin's native input verifier.
use bitcoin::{consensus::deserialize, hex::FromHex, Amount, ScriptBuf, Transaction, TxOut};
use rbitcoin_consensus::{
    block::{InputIndex, ScriptCheckJob, ScriptVerifyFlags},
    script, ConsensusError,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

#[derive(Deserialize)]
struct SpentOutput {
    amount: u64,
    script_pubkey: String,
}

#[derive(Deserialize)]
struct Input {
    tx: String,
    index: usize,
    flags: Vec<String>,
    spent_outputs: Vec<SpentOutput>,
}

fn verify(input: Input) -> Result<Value, Box<dyn std::error::Error>> {
    let tx: Transaction = deserialize(&Vec::from_hex(&input.tx)?)?;
    let index = InputIndex::in_tx(&tx, input.index)?;
    let mut flags = ScriptVerifyFlags::buried(false, false, false, false, false);
    flags.null_dummy = false;
    flags.witness_active = false;
    for name in &input.flags {
        match name.as_str() {
            "P2SH" => flags.bip16_active = true,
            "DERSIG" => flags.bip66_active = true,
            "NULLDUMMY" => flags.null_dummy = true,
            "CHECKLOCKTIMEVERIFY" => flags.bip65_active = true,
            "CHECKSEQUENCEVERIFY" => flags.bip112_active = true,
            "WITNESS" => flags.witness_active = true,
            "TAPROOT" => flags.taproot_active = true,
            _ => return Err(format!("unknown consensus flag: {name}").into()),
        }
    }
    let mut prevouts = Vec::with_capacity(input.spent_outputs.len());
    for output in input.spent_outputs {
        prevouts.push(TxOut {
            value: Amount::from_sat(output.amount),
            script_pubkey: ScriptBuf::from_bytes(Vec::from_hex(&output.script_pubkey)?),
        });
    }
    let job = ScriptCheckJob::try_new(prevouts, tx, flags)?;
    let (sats, script_bytes) = job.prevout_parts(index)?;
    let result = script::verify_input(
        &job,
        index,
        &job.tx,
        &mut None,
        job.pre()?,
        &Default::default(),
        Amount::from_sat(sats),
        script_bytes,
    );
    match result {
        Ok(()) => Ok(json!({"ok":true, "valid":true, "script_error":"OK"})),
        Err(ConsensusError::Script(token)) => {
            Ok(json!({"ok":true, "valid":false, "script_error":token}))
        }
        Err(other) => Ok(json!({"ok":false, "error":"internal", "message":other.to_string()})),
    }
}

fn response(line: &str) -> Value {
    let request: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            return json!({"id":null, "ok":false, "error":"bad_request", "message":e.to_string()})
        }
    };
    let id = request["id"].clone();
    let mut response = match request["op"].as_str() {
        Some("info") => json!({
            "ok":true, "name":"rbitcoin native script", "version":env!("RBITCOIN_REV"),
            "spec_version":"protocol-v1", "ops":["script_verify"]
        }),
        Some("script_verify") => serde_json::from_value(request)
            .map_err(|e| e.into())
            .and_then(verify)
            .unwrap_or_else(|e| json!({"ok":false,"error":"bad_request","message":e.to_string()})),
        _ => json!({"ok":false,"error":"unsupported_op","message":"expected script_verify"}),
    };
    response["id"] = id;
    response
}

fn main() -> io::Result<()> {
    let mut output = io::BufWriter::new(io::stdout().lock());
    for line in io::stdin().lock().lines() {
        let line = line?;
        if !line.trim().is_empty() {
            writeln!(output, "{}", response(&line))?;
            output.flush()?;
        }
    }
    Ok(())
}
