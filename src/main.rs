use clap::Parser;
use wifimap::{
    cli::{Cli, run},
    measure::MeasureError,
};

fn main() -> std::process::ExitCode {
    match run(Cli::parse()) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("Error: {error:#}");
            let code = match error.downcast_ref::<MeasureError>() {
                Some(MeasureError::Cancelled) => 130,
                Some(_) => 2,
                None => 3,
            };
            std::process::ExitCode::from(code)
        }
    }
}
