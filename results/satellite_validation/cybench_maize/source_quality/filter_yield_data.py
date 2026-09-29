import os
import argparse
import pandas as pd

from cybench.config import KEY_LOC, KEY_TARGET, PATH_DATA_DIR


def clean_yield_data(yield_file: str, input_dir: str, output_dir: str):
    """Cleans the yield data by dropping NaN values only in relevant columns and filtering rows where key_target > 0.0, keeping all columns."""
    df = pd.read_csv(yield_file, header=0)
    original_size = len(df)
    df = df.dropna(
        subset=[KEY_LOC, "harvest_year", KEY_TARGET]
    )  # Drop rows where key_target is NaN
    df = df[df[KEY_TARGET] > 0.0]  # Keep only rows where key_target > 0.0
    removed_entries = original_size - len(df)
    yield_output_file = os.path.join(output_dir, os.path.relpath(yield_file, input_dir))
    os.makedirs(os.path.dirname(yield_output_file), exist_ok=True)

    if removed_entries > 0:
        df.to_csv(yield_output_file, index=False)
        print(
            f"Removed {removed_entries} rows and saved filtered data to: {yield_output_file}"
        )
    else:
        # Create a symlink for unmodified files
        if not os.path.exists(yield_output_file):
            print(f"ln -s {yield_file} {yield_output_file}")
            os.symlink(yield_file, yield_output_file)


def process_files(input_dir: str, crops, output_dir: str):
    for crop in crops:
        for country_code in os.listdir(os.path.join(input_dir, crop)):
            file_path = os.path.join(
                input_dir, crop, country_code, f"yield_{crop}_{country_code}.csv"
            )
            if os.path.exists(file_path):
                clean_yield_data(file_path, input_dir, output_dir)


def main():
    parser = argparse.ArgumentParser(description="Clean yield data files.")
    parser.add_argument(
        "--directory",
        type=str,
        default=PATH_DATA_DIR,
        help=f"Root directory to search for crop data (default: {PATH_DATA_DIR}).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="Directory to save the processed and filtered files.",
    )

    args = parser.parse_args()
    # List of crops to process
    crops = ["maize", "wheat"]

    process_files(args.directory, crops, args.output_dir)


if __name__ == "__main__":
    main()
