# Usage

## Do I need a new GitHub repository?

Yes, if you want daily automatic updates and a GitHub Pages reading page.

You can work locally first, then create an empty GitHub repository and push this
directory to it. GitHub Actions will run only after the project is on GitHub.

## First Setup

1. Create a new empty GitHub repository, for example `my-arxiv-daily`.
2. Push this local directory to that repository.
3. Open `Settings -> Actions -> General -> Workflow permissions`.
4. Select `Read and write permissions`.
5. Open `Actions -> Daily arXiv Update -> Run workflow`.

## GitHub Pages

To publish the generated reading page:

1. Open `Settings -> Pages`.
2. Choose `Deploy from a branch`.
3. Select branch `main` and folder `/docs`.
4. The page will be available at `https://<your-user>.github.io/<repo-name>/`.

## Metadata Limitations

The arXiv API includes title, authors, abstract, categories, dates, and links.
It does not reliably include corresponding author or author affiliations.

This repository therefore keeps `corresponding_author` and `first_affiliation`
as explicit fields in `data/papers.json`. They are initialized as `TBD` and can
be manually curated or enriched later with a PDF/HTML parser.
