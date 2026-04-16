# Measuring Data Reuse in Open Neuroscience: A Systematic Analysis of the DANDI Archive

## Abstract

Open neurophysiology data repositories have emerged as critical infrastructure for neuroscience, yet the scientific return on investment from data sharing remains poorly quantified. Here we present a comprehensive, automated pipeline for measuring secondary data reuse on the DANDI Archive, the primary open repository for Neurodata Without Borders (NWB) electrophysiology and imaging data. We identified 554 public, non-empty dandisets on DANDI as of April 2026, of which 359 (65%) could be linked to 347 unique primary papers, 204 through formal dandiset metadata and 155 through large language model (LLM) identification with DOI validation. Using OpenAlex, we screened 14,672 unique papers citing those primary papers, generating 18,854 paper-dandiset pairs for LLM classification. We identified 1,234 reuse events from 944 unique papers (6.5% of citations), of which 881 (71%) originated from independent laboratories and 339 (28%) from the original depositing group. Reuse was most commonly for tool demonstration (25%), novel analysis (19%), and data aggregation (15%), with benchmarking, confirmatory analysis, simulation, machine learning training, and teaching accounting for the remainder. Survival analysis revealed that different-laboratory reuse peaks approximately 3.9 years after data deposition, following a Richards growth model (K = 2.1 papers per dandiset). Andersen-Gill Cox regression identified primary-paper citation count (HR = 4.5 per 10-fold increase), Allen Institute provenance (HR = 6.1), and human or non-human primate species (HR = 3.1 and 2.0, respectively) as the strongest predictors of reuse, while dataset size had minimal effect. Strikingly, 92% of reuse papers cited only the primary paper and not the dataset itself, demonstrating a fundamental limitation of citation-based dataset impact metrics. These results provide the first systematic, field-wide estimate of neurophysiology data reuse and establish a reproducible methodology applicable to other open data repositories.

---

## 1. Introduction

Scientific data sharing has transitioned in a decade from a community aspiration to a regulatory requirement. The United States National Institutes of Health (NIH) finalized its Data Management and Sharing Policy in 2023, mandating that investigators prospectively plan for sharing scientific data generated with NIH funding [@NIH2020DMS; @Ross2023DataSharing]. The NIH BRAIN Initiative, the primary federal funder of large-scale neurophysiology, has gone further, requiring data sharing as a condition of award since 2020 and supporting the development of domain-specific infrastructure to receive it [@NIMH2019BRAIN; @Iyer2024BRAINEcosystem]. Similar mandates have emerged from the European Research Council [@ERC2016OpenAccess], the Wellcome Trust [@Wellcome2017DataPolicy], and a growing coalition of journals adopting data availability requirements as a condition of publication [@Vasilevsky2017JournalPolicies; @Stodden2013JournalPolicies]. Underlying all of these policies is a hypothesis that has proved remarkably difficult to test empirically: that shared data will be reused, and that reuse will generate scientific value commensurate with the cost of sharing.

The DANDI Archive (Distributed Archives for Neurophysiology Data Integration) was launched in 2019–2020 as a BRAIN Initiative-funded platform for storing and disseminating neurophysiology data standardized to the Neurodata Without Borders (NWB) format [@Rubel2022NWB; @Magland2025DANDITools]. NWB provides a self-describing HDF5/Zarr-based file format that encodes not just raw electrophysiology and imaging traces but also the behavioral metadata, electrode geometry, and spike-sorted unit tables needed to make a dataset independently interpretable [@Teeters2015NWB]. As of April 2026, DANDI hosts over 554 publicly accessible, non-empty dandisets, structured data packages that may contain terabytes of extracellular recordings, calcium imaging movies, intracellular electrophysiology, or multimodal combinations thereof. DANDI assigns persistent identifiers (DANDI DOIs via DataCite) to each dandiset and, through its metadata schema, encourages depositors to link dandisets to associated publications. This infrastructure makes DANDI well-suited to bibliometric analysis of data reuse, yet no such analysis had previously been attempted at the scale of the entire archive.

Measuring secondary data use is methodologically harder than it appears. The most obvious approach, counting direct citations of dataset DOIs, severely underestimates actual reuse because most investigators who reuse a public dataset cite the primary paper describing the experiment, not the repository record housing the data [@Cousijn2018DataCitation; @Borgman2012Conundrum]. This was documented in genomics by Piwowar and Vision, who showed that over half of gene expression studies deposited in NCBI's Gene Expression Omnibus (GEO) were subsequently reused by independent groups, with a majority of reuse papers citing only the primary article [@Piwowar2013DataReuse]. Analogous observations have been made for astronomical survey data [@Henneken2012AstronomyData] and ecological datasets deposited in repositories such as the Knowledge Network for Biocomplexity (KNB) and Dryad [@Roche2015EcologyArchiving; @Wallis2013ShareData]. In each domain, conventional citation counting missed the majority of actual reuse events. Studies that manually audited reuse found substantially higher rates but could not scale beyond a few hundred datasets [@Hemphill2022DataReuse]. Automated approaches employing keyword search of full-text corpora improved scale but were plagued by false positives, particularly when dataset identifiers overlapped with anatomical nomenclature or gene symbols [@Zhao2018DataSetMentions; @Lafia2023HowWhyReference]. No systematic, field-wide analysis of neurophysiology data reuse existed prior to the present work.

Neurophysiology data are particularly expensive to generate, a single electrophysiology session with multiple implanted probes may require months of animal training, weeks of surgical preparation, and days of recording, making the cost-benefit calculus of mandated sharing an important policy question [@Steinmetz2018Neuropixels; @Jun2017Neuropixels]. At the same time, neurophysiology datasets have characteristics that make reuse scientifically attractive: standardized electrode arrays, common preprocessing pipelines, and the NWB format itself mean that a dataset acquired for one scientific question can plausibly be reused to test a different hypothesis, validate a new spike-sorting algorithm, or serve as a benchmark for a neural decoding method. Whether this scientific potential is being realized in practice has been unknown.

Here we address this gap with the first comprehensive, automated, LLM-assisted pipeline for tracking data reuse across an entire neurophysiology repository. Our approach proceeds in three stages. First, we link dandisets to their primary papers using a combination of structured metadata extraction and LLM-based identification of informal relationships, covering 65% of all public DANDI dandisets. Second, we use the OpenAlex citation graph to enumerate all papers citing those primary publications and retrieve full text via a multi-source pipeline spanning Europe PMC, NCBI PubMed Central, Unpaywall, and direct publisher scraping. Third, we deploy a large language model (Google Gemini 3 Flash, via OpenRouter) to classify each paper–dandiset pair as genuine data reuse, a non-data-use citation, or neither, with the model reasoning over citation context excerpts and, when available, extended passage text. We validate the pipeline against manually curated ground-truth examples and quantify the false-positive rate using a DOI-existence filter and confidence thresholding.

Applied to the complete DANDI Archive, this pipeline reveals that neurophysiology data reuse is real, measurable, and growing, but slower and less frequent than proponents of open data mandates have assumed. Different-laboratory reuse constitutes the majority of reuse events, and the mean cumulative number of reuse papers per dandiset follows a Richards growth curve that peaks in rate roughly four years after public data release. The dominant reuse modality is tool demonstration, in which a new computational method is validated against an established public dataset, suggesting that the primary beneficiaries of open neurophysiology data have so far been algorithm developers rather than biological hypothesis testers. Finally, we document a systematic failure of conventional citation metrics: 92% of reuse papers do not cite the dataset record itself, meaning that DOI-based impact tracking, as currently implemented by DataCite and recommended by FORCE11 data citation principles, would miss the vast majority of scientifically meaningful reuse. These findings have direct implications for how funding agencies should evaluate the impact of data-sharing mandates and how repositories should design attribution infrastructure.

---

## 2. Methods

### 2.1 Linking Dandisets to Primary Papers

The DANDI Archive exposes a REST API at `api.dandiarchive.org/api` through which all published dandiset metadata can be retrieved with pagination. We queried this endpoint to obtain the full catalog and inspected each dandiset's `relatedResource` list for links to primary publications. Four DataCite relation types were treated as indicators of a primary-paper relationship: `dcite:IsDescribedBy` (the most prevalent, indicating the dataset is described by a publication), `dcite:IsPublishedIn`, `dcite:IsSupplementTo`, and `dcite:Describes` (the logical inverse of `IsDescribedBy`, discovered after identifying 16 dandisets that used this inverted convention). Resources were filtered to paper types only; if a `resourceType` field was present it had to be one of `dcite:JournalArticle`, `dcite:Preprint`, `dcite:DataPaper`, `dcite:ConferencePaper`, or `dcite:ConferenceProceeding`, while resources typed as Software, Dataset, or ComputationalNotebook were excluded.

DOIs were extracted directly from each resource's `identifier` field or derived from publisher URLs where no explicit DOI was recorded. Publisher-specific URL patterns were used to recover DOIs for eLife (`elifesciences.org/articles/{id}` → `10.7554/eLife.{id}`) and Nature (`nature.com/articles/{slug}` → `10.1038/{slug}`), with a generic DOI-pattern regex applied as a fallback for other publishers. In addition, each dandiset's free-text description field was searched for DOI patterns (matching the `10.XXXX/...` format), capturing cases where depositors mentioned their associated publication in the description without creating a formal `relatedResource` entry.

For the 350 dandisets that had no formal `relatedResource` links and no DOIs in their description, we queried the Gemini 3 Flash model via the OpenRouter API, providing each dandiset's name, description text (truncated to 2,000 characters), and up to 15 contributor names. The model was prompted to identify the most likely associated publication and return a structured JSON response including a DOI, title, authors, journal, year, and a confidence score from 1 to 10; only responses with confidence ≥ 6 were retained.

All candidate DOIs, whether extracted from metadata or proposed by the LLM, were validated by resolving them against CrossRef and OpenAlex. DOIs that failed resolution were subjected to a title-based recovery procedure that searched sequentially through Europe PMC (full-text query), OpenAlex (title filter), and CrossRef (title query with majority-word-overlap matching). Of the LLM-proposed DOIs, 175 were validated successfully and 106 were rejected as hallucinations. After merging all sources, 359 of the 554 non-empty dandisets (65%) were linked to 347 unique primary papers.

### 2.2 Citation Discovery and Text Retrieval

For each primary paper identified in Section 2.1, we used the OpenAlex API [@Priem2022OpenAlex] to retrieve the full list of papers that cited it. Citing papers were filtered to those with a publication date on or after the dandiset's creation date, the assumption being that any reuse necessarily postdates data availability. Citation metadata including DOI, title, publication date, and journal were collected for each citing paper.

Full text was retrieved through a sequential cascade of seven sources, attempting each in order until text was obtained. The primary source was Europe PMC, queried via its XML full-text API, which provides structured article content for papers in PubMed Central. For papers not available through Europe PMC, we attempted NCBI PubMed Central via the Entrez `efetch` endpoint. Third, CrossRef metadata was retrieved and used when structured reference lists were sufficient for classification purposes. For papers with DOIs beginning with `10.1016/` (Elsevier journals), the ScienceDirect full-text API was queried using a dedicated API key. Papers accessible via open access were retrieved as PDFs through the Unpaywall API. Direct HTML scraping of publisher pages was attempted as a sixth option. Finally, for bioRxiv and medRxiv preprints, a Playwright browser automation was used to render and extract content from the JavaScript-heavy preprint interfaces.

This cascade achieved full-text retrieval for approximately 92% of citing papers. The remaining 8% without text were disproportionately recent publications: 73% were from 2024–2026, reflecting indexing delays at Europe PMC and other full-text sources rather than permanent inaccessibility. Among the remainder, Elsevier journals accounted for the largest share of paywalled papers not retrievable through the ScienceDirect API.

A parallel pipeline targeted papers that directly cited dandisets by DOI or URL (rather than citing the primary paper). Europe PMC full-text search was used to identify papers containing DANDI Archive DOI patterns (`10.48324/dandi`) or dandiset URL patterns (`dandiarchive.org/dandiset`), and each match was retrieved and processed through the same text-retrieval cascade.

### 2.3 Reuse Classification

Each citing paper was evaluated against each dandiset it potentially reused using the Gemini 3 Flash model via OpenRouter. The classification prompt presented up to three citation context excerpts (passages of text surrounding the location in the citing paper where the primary paper's reference number or author-year citation appeared), together with the dandiset identifier and name. Citation contexts were located by a multi-strategy algorithm that matched numbered reference markers (e.g., `[1]`, `[1,2]`), author-year patterns (e.g., `Smith et al., 2020`), and direct DOI occurrences within the paper text, then extracted a window of surrounding sentences.

The model was asked to assign one of three labels: **REUSE** (the citing paper downloaded and analyzed the actual recorded data), **MENTION** (the primary paper was cited as prior work or background without reusing the data), or **NEITHER** (the citation was a parsing artifact or otherwise irrelevant). Seven explicit rules were included in the prompt to reduce false positives: (1) adopting software, code, or analytical methods from the primary paper is not data reuse; (2) the citing paper must have used data from the specific dataset in question, not a different dataset that the primary paper is cited for context about; (3) parenthetical citations supporting general factual statements are not reuse; (4) simulations parameterized from a described cell type but not using the actual recorded data are not reuse; (5) papers that collected their own new data while citing the primary paper for comparison are not reuse; (6) using a figure or summary statistic for comparison is not reuse; and (7) for texts shorter than 15,000 characters (typically abstract-only retrievals), confidence was capped at 5 to prevent classification of ambiguous short fragments as REUSE.

Each classification included a numeric confidence score from 1 to 10. Same-lab status was assessed for REUSE entries by checking author name overlap between the citing paper and the primary paper's author list. Results were cached per paper-dandiset pair to avoid redundant API calls across pipeline runs.

### 2.4 Source Archive and Reuse Type Classification

For each entry classified as REUSE, the data source archive was identified in two phases. First, explicit archive names in the initial classification response were normalized to canonical forms (e.g., "Neural Latents Benchmark" → "DANDI Archive", "IBL database" → "IBL"). Second, entries whose source archive remained "unclear" were re-evaluated with a focused Gemini 3 Flash prompt that extracted data availability sections, STAR Methods, Key Resources Tables, and data-access mentions from the full paper text before querying the model.

Because some categories remained uncertain after LLM classification, constrained estimation was applied to the residual "unclear" pool. The Allen Institute count was capped at the number of dandisets in the unclear pool that were actually sourced from Allen Institute data, as identified through manual spot-checking. The Neural Latents Benchmark, which is exclusively hosted on DANDI, was assigned to DANDI Archive. Remaining unclear entries were allocated proportionally to the observed distribution of resolved archives.

Each REUSE entry was also assigned one of eight reuse-type categories by a second Gemini 3 Flash prompt: **TOOL_DEMO** (showcasing a new analysis tool or pipeline), **NOVEL_ANALYSIS** (applying a new scientific question to existing data), **AGGREGATION** (combining multiple datasets), **BENCHMARK** (evaluating algorithmic performance), **CONFIRMATORY** (replicating findings with independent data), **SIMULATION** (using real data to constrain computational models), **ML_TRAINING** (training machine learning models), and **TEACHING** (educational use). The prompt provided the reuse reasoning from the first classification step and up to three text excerpts, and requested the single primary category. These categories are a domain-specific refinement of the Activity Type dimension in the FORCE11 Data Usage Typology [@Bobrov2025FORCE11Typology], tailored to capture distinctions relevant to neurophysiology (e.g., tool demonstration vs. novel scientific analysis) that are not differentiated in the generic framework.

### 2.5 Temporal Modeling

The accumulation of reuse papers over a dandiset's lifetime was modeled as a recurrent event process. The Mean Cumulative Function (MCF), estimated via the Nelson-Aalen estimator adapted for recurrent events, quantifies the expected cumulative number of reuse papers per dandiset as a function of dandiset age. At each observed event time $t_i$, the MCF increment is $1/n_{\text{at risk}}(t_i)$, where $n_{\text{at risk}}(t_i)$ is the number of dandisets with age $\geq t_i$, correctly accounting for right-censoring due to the varying ages of dandisets in the corpus. Same-lab and different-lab reuse were modeled separately, as they exhibit qualitatively distinct accumulation dynamics.

The different-lab MCF was fitted with a Richards generalized logistic function constrained to pass through the origin:

$$\text{MCF}(t) = \frac{K}{\left(1 + \nu \cdot e^{-r(t - t_0)}\right)^{1/\nu}} - \text{offset}$$

where $K$ is the carrying capacity (maximum expected reuses per dandiset), $r$ is the growth rate, $t_0$ is the inflection point in years, $\nu$ controls asymmetry, and the offset enforces the boundary condition $\text{MCF}(0) = 0$. The same-lab MCF was fitted with a saturating exponential $\text{MCF}(t) = K(1 - e^{-t/\tau})$, where $\tau$ is the characteristic time constant and $K$ is the asymptotic value.

Uncertainty in binned reuse rates was quantified using exact Poisson confidence intervals based on the chi-squared method, appropriate for count data with small expected values.

In addition to the MCF analysis of recurrent reuse, we performed standard Kaplan-Meier survival analysis on the time to first different-lab reuse for each dandiset. Dandisets that had not yet been reused by the analysis cutoff date were treated as right-censored observations. This analysis estimates the probability that a dandiset will receive at least one different-lab reuse paper within a given time after creation (Figure S6).

Future dandiset creation was projected using a power-law fit $N(t) = a \cdot t^b$ to the cumulative dandiset count as a function of time since the archive's founding. Total expected reuse papers at future times were obtained by convolving the dandiset creation trajectory with the MCF model: for each existing and projected dandiset, the MCF was evaluated at its age at each future time point and the contributions summed. An OpenAlex indexing delay of approximately six months was applied as a data cutoff to avoid downward bias in recent bins.

### 2.6 Predictors of Reuse: Andersen-Gill Regression

To identify dandiset-level features associated with higher rates of different-lab reuse, we fitted an Andersen-Gill extension of the Cox proportional hazards model [@Andersen1982CoxReg], which generalizes standard survival analysis to recurrent events. Each dandiset contributed a series of counting-process intervals: time runs from dandiset creation to the analysis cutoff, punctuated by reuse events. A dandiset that attracted three different-lab reuse papers at ages 12, 24, and 36 months thus contributed four intervals (one per inter-event gap, plus a final right-censored interval to the cutoff). The hazard for dandiset $i$ at time $t$ was modeled as:

$$h_i(t) = h_0(t) \exp(\boldsymbol{\beta}^\top \mathbf{x}_i)$$

where $h_0(t)$ is an unspecified baseline hazard and $\mathbf{x}_i$ is a vector of time-fixed covariates. Eleven covariates were included: three species indicators (mouse, human, non-human primate; reference category: rat/other), two modality indicators (electrophysiology, imaging; reference: other/multimodal), log-transformed primary-paper citation count, log-transformed dataset size (GB), log-transformed number of subjects, journal impact (log-transformed h-index of the primary paper's journal, obtained from the OpenAlex source endpoint), and binary indicators for Neural Latents Benchmark (NLB) datasets and Allen Institute datasets. Citation count was included as a proxy for the visibility and scientific impact of the associated primary paper. Journal h-index, a continuous measure of the publishing venue's prestige, replaced a binary high-impact indicator to avoid arbitrary threshold choices. The model was fitted using the `lifelines` Python library with the Breslow method for ties.

---

## 3. Results

### 3.1 Scale of Reuse

Our pipeline identified 1,234 paper-dandiset reuse events from 944 unique citing papers across the DANDI Archive, after deduplicating preprint/published pairs (Figure 1). These events span the full diversity of the archive: 881 (71%) were classified as different-lab reuse, in which the citing authors have no institutional or authorship overlap with the depositing team, while the remaining 339 (28%) represent same-lab reuse, in which the original data creators published subsequent analyses of their own deposited datasets.

### 3.2 Most Reused Datasets

The ten most-reused dandisets account for a disproportionate fraction of all recorded different-lab reuse (Table 1). Allen Institute datasets account for five of the top ten, reflecting the institute's investment in large-scale, systematically collected datasets.

**Table 1.** Ten most-reused dandisets by different-lab reuse count.

| Rank | Dandiset ID | Name | Different-lab reuse |
|------|-------------|------|---------------------|
| 1 | 000253 | Allen Institute OpenScope, Global/Local Oddball | 89 |
| 2 | 000768 | AIBS Patchseq, nonhuman primate | 80 |
| 3 | 000020 | Patch-seq recordings from mouse visual cortex | 67 |
| 4 | 000017 | Distributed coding of choice, action and engagement (Steinmetz) | 43 |
| 5 | 000049 | Allen Institute, TF x SF tuning in mouse visual cortex | 43 |
| 6 | 000070 | Neural population dynamics during reaching (Churchland) | 41 |
| 7 | 000008 | Phenotypic variation across transcriptomic cell types (Allen) | 35 |
| 8 | 000402 | MICrONS Two Photon Functional Imaging | 29 |
| 9 | 000129 | MC_RTT: macaque motor cortex spiking activity | 28 |
| 10 | 000044 | Diversity in neural firing dynamics | 27 |

### 3.3 Reuse Types

The 1,234 reuse events were further classified into eight functional categories describing the primary purpose for which the data were reused (Figure 3). Tool and method demonstration was the most prevalent category, accounting for 334 pairs (25.6%), and was especially dominant among different-lab reuse events. For example, Loewinger et al. [-@Loewinger2024FLMM] used the Jeong et al. [-@Jeong2022Dopamine] fiber photometry dataset (DANDI:000351) to demonstrate a new statistical framework for trial-level temporal dynamics, and Huang et al. [-@Huang2022VDGLVM] applied their variational dynamic graph latent variable model to Neural Latents Benchmark data (DANDI:000128).

Novel analysis, applying a new scientific question to an existing dataset, accounted for 246 pairs (18.8%) overall and was the single most common motivation for same-lab reuse (33%). Tallman et al. [-@Tallman2025Allocation] used the Chandravadia et al. [-@Chandravadia2020SingleNeuron] human hippocampal recording dataset (DANDI:000004) to investigate neuronal allocation and sparse coding of episodic memories, a question not addressed in the original study, while Li et al. [-@Li2023ThetaAlpha] re-analyzed the Boran et al. [-@Boran2022PersistentFiring] human intracranial dataset (DANDI:000575) to study theta-alpha connectivity during working memory.

Aggregation (196 pairs, 15.0%) involved combining DANDI datasets with other data sources. Zhuo et al. [-@Zhuo2024MAPbrain] integrated primate Patch-seq data from Jorstad et al. [-@Jorstad2023Primate] (DANDI:000768) into MAPbrain, a multi-omics atlas, and Phung et al. [-@Phung2025CellTypes] pooled 388 datasets from 36 studies, including DANDI data (DANDI:001464), to identify cell types associated with brain phenotypes.

Benchmark use (163 pairs, 12.5%), confirmatory reuse (163 pairs, 12.5%), and simulation use (150 pairs, 11.5%) were roughly equally prevalent. Benchmark examples include evaluating neural decoding algorithms against Neural Latents Benchmark datasets (DANDI:000128, DANDI:000129, DANDI:000130). Confirmatory reuse involved using DANDI data as independent validation: Fink et al. [-@Fink2025Inhibitory] used the MICrONS electron microscopy dataset [@MICrONS2021Connectomics] (DANDI:000402) to confirm their own findings on inhibitory synapse reorganization, and Doykos et al. [-@Doykos2025Orienting] used IBL data (DANDI:000149) to corroborate superior colliculus recordings. Simulation studies used DANDI data to constrain computational models: Amsalem et al. [-@Amsalem2022Subthreshold] used the Scala et al. [-@Scala2020Phenotypic] morphological reconstructions (DANDI:000008) to parameterize multi-compartment biophysical simulations, and Morrison et al. [-@Morrison2025CElegans] fitted a *C. elegans* network model to the Randi et al. [-@Randi2023CElegans] calcium imaging data (DANDI:000776).

Machine-learning training accounted for 58 pairs (4.4%). He et al. [-@He2025EMDINO] curated the MICrONS dataset [@MICrONS2021Connectomics] (DANDI:000402) into a 5-million-image training set for a foundation model for electron microscopy, and Bahl et al. [-@Bahl2022NEUROeSTIMator] trained a neural network on the Gouwens et al. [-@Gouwens2020Integrated] Patch-seq data (DANDI:000020) to predict activity-dependent gene expression. Teaching use was rare in publications (4 pairs, 0.3%), represented by resources such as the Neuromatch Academy online summer school [@tHart2022Neuromatch], which uses the Steinmetz et al. [-@Steinmetz2019Distributed] dataset (DANDI:000017) for student projects, and nwb4edu [@PriceWhelan2025nwb4edu], an online textbook built around DANDI datasets.

The contrast between different-lab and same-lab reuse motivations reflects a fundamental distinction: outside labs predominantly use DANDI data as a substrate for methods development and benchmarking, while the depositing labs themselves continue to mine their datasets for new scientific insights.

### 3.4 Source Archives

Because DANDI operates as one node within a broader ecosystem of neuroscience data repositories, determining what fraction of reuse papers actually accessed data from DANDI, as opposed to a parallel copy hosted on the Allen Institute data portal, CRCNS, the International Brain Laboratory (IBL) data release, Figshare, CELLxGENE, or MICrONS Explorer, required explicit source-archive classification. Among different-lab reuse papers for which a source archive was determinable, DANDI Archive was explicitly named in approximately 22% of cases (Figure 2, Panel A). The Allen Institute data portals constituted the largest identifiable non-DANDI source, reflecting the many Allen Institute datasets that are mirrored on DANDI but were accessed via the institute's own portals.

A substantial proportion of papers, approximately 30-40%, did not name a specific archive, instead citing only the primary publication or providing a general description of the data. To produce a conservative lower bound on DANDI-specific reuse, we applied a constrained estimation procedure: papers reusing Neural Latents Benchmark (NLB) datasets were assigned to DANDI (the NLB datasets are hosted exclusively on DANDI), papers associated with Allen Institute dandiset identifiers were capped at the observed unclear count from those dandisets, and the remaining unclear papers were distributed proportionally across the known non-Allen archives. Under this estimation, approximately 30% of otherwise-unclear papers are estimated to have accessed data from DANDI (Figure 2, Panel A, dashed bars).

Among different-lab reuse papers, 35% were published as preprints at the time of analysis, with bioRxiv accounting for 25% of all different-lab reuse papers, the single most common venue (Figure 2, Panel B). Among peer-reviewed journals, eLife was the most common venue (12%), followed by Nature Communications (4.4%) and PLOS Computational Biology (3.2%). The dominance of open-access journals is notable and suggests that the culture of data openness that motivates dataset sharing may also predispose researchers toward open-access publication.

### 3.5 Temporal Dynamics

The rate at which a given dandiset attracts different-lab reuse papers follows a characteristic rise-and-fall pattern (Figure 2, Panels E-F; Figure 4, Panels A-B). Modeling the empirical per-dandiset reuse rate using a generalized Richards curve yielded best-fit parameters of K = 2.1, r = 2.1, t_0 = 3.9 years, and v = 4.0 for different-lab reuse. The peak reuse rate occurs approximately 3.9 years after a dandiset is first made publicly accessible. The asymmetry parameter v = 4 indicates that the rise toward peak reuse is approximately four times faster than the subsequent decline.

Same-lab reuse follows a qualitatively different pattern: the reuse rate is highest immediately following dataset creation and decays approximately exponentially, with a characteristic time constant t ~ 12 years.

Integrating the fitted MCF over the full observable lifetime, each deposited dataset generates an expected 2.1 different-lab and 2.9 same-lab reuse papers. Kaplan-Meier survival analysis of time to first different-lab reuse shows that approximately 25% of dandisets are reused within 3 years, and approximately 50% within 5 years (Figure S6).

It should be noted that the DANDI Archive was founded in 2019-2020, meaning that even the oldest dandisets have at most six years of observable reuse history. This limited longitudinal window constrains the modeling, particularly on the declining (right) side of the Richards curve: the rise phase is well-characterized by the data, but the rate of obsolescence-driven decline remains uncertain. The fitted carrying capacity K = 2.1 and the asymmetry parameter v = 4.0 should therefore be treated as preliminary estimates that will require refinement as the archive matures and longer reuse trajectories become observable.

### 3.6 Predictors of Different-Lab Reuse

An Andersen-Gill Cox proportional hazards model identified several dandiset-level features significantly associated with rates of different-lab reuse (Figure 5). The strongest predictor was Allen Institute provenance (HR = 6.1, p < 0.001), indicating that Allen Institute dandisets attract reuse at approximately six times the rate of comparable non-Allen datasets after controlling for all other covariates. Primary-paper citation count was the second-strongest predictor (HR = 4.5 per 10-fold increase in citations, p < 0.001), confirming that the visibility of the associated publication is a key driver of downstream data reuse. Human data (HR = 3.1, p < 0.001), non-human primate data (HR = 2.0, p < 0.001), and electrophysiology modality (HR = 1.6, p < 0.001) were all associated with significantly higher reuse rates. Journal impact factor, measured as the log-transformed h-index of the primary paper's publishing venue, was also a significant positive predictor (HR = 1.5 per 10-fold increase in h-index, p < 0.01). Mouse data showed a modest positive association (HR = 1.5, p = 0.01). Dataset size had a small but significant effect (HR = 1.1 per 10-fold increase in GB, p = 0.003), while number of subjects was not significant (p = 0.10).

Imaging modality was associated with significantly lower reuse rates (HR = 0.5, p < 0.001), likely reflecting the younger age of imaging datasets on DANDI and the higher computational barriers to reusing calcium imaging data compared to spike-sorted electrophysiology. Benchmark status (NLB) was not a significant independent predictor (HR = 0.8, p = 0.36) after controlling for citation count and Allen Institute status, suggesting that the high reuse of NLB datasets is largely explained by their high-visibility publications rather than their benchmark designation per se. The model achieved a concordance index of 0.63.

### 3.7 Download Metrics vs. Publication-Based Reuse

To cross-validate our citation-based reuse metric, we compared it with DANDI's download access logs, which record total bytes transferred, unique download days, and geographic reach (unique regions and countries) for each dandiset (Figure S7). After excluding test and empty dandisets, total download volume showed the weakest correlation with reuse (Pearson r = 0.18 on log-transformed bytes, p < 10^-4), largely because download volume reflects dataset size rather than the number of independent users. Normalizing by dataset size (download volume / dataset volume) did not improve the correlation (r = 0.18), confirming that the disconnect is not simply an artifact of large files inflating byte counts. Download frequency and geographic reach were slightly stronger but still weak predictors (r = 0.24 and r = 0.25, respectively; both p < 10^-7). The two metrics identify substantially different datasets as high-impact: only 2 of the top 10 most-downloaded dandisets (by unique download days) appeared in the top 10 most-reused.

Several dandisets with hundreds or thousands of unique download days had zero different-lab reuse papers (e.g., DANDI:000126 with 1,584 download days, DANDI:000108 with 1,274 download days). These datasets are likely accessed by automated pipelines, continuous integration systems, or infrastructure mirroring rather than for scientific analysis. Conversely, the most-reused dandiset (DANDI:000253, 89 different-lab reuse papers) had only 429 download days. These findings demonstrate that download volume and publication-based reuse capture fundamentally different dimensions of data impact, and that neither metric alone provides a complete picture of a dataset's scientific value.

### 3.8 The Dataset Citation Gap

A striking feature of these reuse records is the near-universal reliance on indirect citation as the mechanism of attribution: 92% of reuse papers cite the primary journal article associated with the dandiset rather than the dataset itself. Only 8% of reuse papers include a direct dataset identifier such as a DANDI DOI or archive URL in their text. This finding underscores both the centrality of the citation graph as the practical infrastructure for tracking data reuse, and the current weakness of dataset-level attribution norms in the neuroscience literature. The citation-based pipeline used here, beginning from DANDI dandisets, identifying their associated primary papers, retrieving all citing works, and applying large-language-model classification to distinguish reuse from mere mention, was therefore essential for obtaining a comprehensive picture of archive-wide reuse (Figure 1).

### 3.9 Growth and Projections

The volume of published data reuse has grown substantially since the archive's inception. Across all labs, only 6 different-lab reuse papers were recorded in 2020; this number rose to 127 in 2025 (Figure 2, Panels C-D; Figure 4, Panels C-D).

The rate of new dandiset creation follows a super-linear power law, N ~ t^1.64, indicating that new deposits are accelerating. Combining the projected trajectory of dandiset creation with per-dandiset reuse rates estimated from the MCF models, we project approximately 860 different-lab and 500 same-lab estimated DANDI reuse papers by April 2029, subject to the assumption that current deposit and reuse trends continue (Figure 4, Panels C-D).

---

## 4. Discussion

### 4.1 Open Neuroscience Data Is Being Reused at Scale

We identified 1,234 reuse events involving 944 unique papers across the DANDI Archive, a substantial body of secondary data use that has grown continuously since the repository's founding. Of these reuse events, 71% originated from groups with no authorship overlap with the original data depositors, confirming that the observed activity constitutes genuine external impact rather than self-citation or intra-lab replication.

The scale of reuse we observe is broadly consistent with findings from analogous studies in other data-sharing ecosystems. Piwowar and Vision [-@Piwowar2013DataReuse] demonstrated that papers sharing microarray data in public repositories received substantially more citations than those that did not. Colavizza et al. [-@Colavizza2020CitationAdvantage] subsequently showed that papers with open data statements in biomedical literature received a citation advantage of approximately 25%. Our findings extend this picture to neuroscience, where the NWB data standard and DANDI's infrastructure have lowered the barriers to interoperability that historically limited cross-lab reuse of electrophysiology and imaging data.

The temporal trend is striking. We recorded 6 reuse events from papers published in 2020, rising to 127 in 2025, a nearly 20-fold increase over five years. The trajectory shows no sign of plateauing, suggesting that the reuse dividend from current data deposits will continue to compound over time.

Cross-validation against DANDI's download access logs confirmed that publication-based reuse and download metrics capture different dimensions of data impact. Download volume, frequency, and geographic reach all correlated weakly with reuse (Pearson r = 0.18-0.25 on log-transformed metrics), and the most-downloaded and most-reused datasets were largely disjoint (2/10 overlap in top 10). Heavy download traffic often reflects automated access (mirroring, CI pipelines, tool testing) rather than scientific reuse. Repositories seeking comprehensive impact assessment will need to combine download analytics with citation-based approaches like the one developed here.

### 4.2 Reuse Types Reflect a Healthy Ecosystem

The eight reuse categories reveal a scientifically diverse landscape. Tool demonstration (25%) drives methodological innovation. Novel analysis (19%) represents reuse in its most scientifically impactful form, extracting new knowledge from existing recordings without additional animal experiments. ML training (4%) is currently modest but is among the fastest-growing categories, as foundation models for neural data emerge. Teaching use, while rare in publications (0.3%), is almost certainly underrepresented by a publication-based methodology: instructors using DANDI datasets in courses or workshops have little reason to publish about it, so the true volume of educational reuse is likely far higher than our pipeline can detect.

### 4.3 The Multi-Archive Ecosystem

Many of the datasets hosted on DANDI are simultaneously available through multiple repositories. Our source archive classification found that only approximately 22% of reuse papers explicitly stated they accessed data from DANDI. This complicates simple interpretations of DANDI's impact but does not diminish it. DANDI's value extends beyond direct downloads: by enforcing NWB format compliance, assigning persistent dandiset DOIs, and indexing metadata in a machine-readable schema, DANDI ensures that datasets meet FAIR principles [@Wilkinson2016FAIR] in ways that informal lab website mirrors cannot.

### 4.4 The Discovery Lag

A consistent feature of the delay distribution is a peak in different-lab reuse activity at approximately four years after dandiset creation. The shape is asymmetric: reuse accumulates relatively quickly in the first two to three years as researchers discover newly available datasets, then declines gradually, likely reflecting a combination of dataset aging, methodological obsolescence, and the emergence of successor datasets.

The 2-4 year discovery lag has direct implications for how funders and policymakers should interpret short-horizon evaluations of data sharing mandates. If a dataset is deposited in 2024 and an impact assessment is conducted in 2026, the majority of its reuse potential will not yet be visible in the publication record. Evaluation frameworks for open data policies must account for this temporal structure, or they will systematically undervalue the return on investment from data sharing requirements.

### 4.5 Publication Visibility is the Strongest Modifiable Predictor

The Andersen-Gill regression analysis provides actionable insight into what drives different-lab reuse. While Allen Institute provenance is the single strongest predictor (HR = 6.1), it is not modifiable by individual investigators. The most actionable finding is the outsized role of primary-paper citation count (HR = 4.5 per 10-fold increase): a dataset associated with a widely cited paper attracts reuse at nearly five times the rate of one associated with a less-cited paper, controlling for species, modality, and dataset size. Journal impact factor independently contributes (HR = 1.5), suggesting that both the venue and the reception of the primary paper matter.

This finding has practical implications for repositories and funders. Repositories seeking to maximize reuse should prioritize onboarding data from high-impact research programs whose publications will naturally attract attention to the associated datasets. Funders evaluating the impact of data sharing mandates should recognize that the primary lever for increasing reuse is not the size of datasets but the scientific prominence of the associated research. The non-significance of the NLB benchmark indicator, after controlling for citations, further supports this interpretation: benchmark datasets are highly reused because they are associated with highly cited papers, not because of the benchmark label itself.

The substantial species effects are also noteworthy. Human (HR = 3.1) and non-human primate (HR = 2.0) datasets are reused at significantly higher rates than mouse data, even after controlling for citation count and other covariates. These are also among the most expensive data to collect: human intracranial recordings require neurosurgical access, and non-human primate experiments involve years of animal training and surgical preparation. The high reuse rates for these species suggest that data sharing mandates deliver disproportionate return on investment for expensive-to-collect data, precisely the regime where avoiding redundant data collection matters most.

The negative association of imaging modality with reuse (HR = 0.5) warrants attention. This may reflect both the relative youth of DANDI's imaging collections and genuine barriers to reusing calcium imaging data, including large file sizes, heterogeneous preprocessing pipelines, and the lack of standardized spike-sorting equivalents for imaging data. As the imaging community develops more mature analysis standards and as imaging datasets on DANDI age, this gap may narrow.

### 4.6 The Dataset Citation Gap

One of the most practically significant findings is the near-complete absence of direct dataset citations in secondary papers. Across our corpus, 92% of reuse papers cited only the associated primary publication, while just 8% included a direct citation to the dandiset DOI or URL. This gap fundamentally undermines the discoverability of reuse and the ability of data depositors to receive appropriate credit.

The problem is systemic. Cousijn et al. [-@Cousijn2018DataCitation] showed that data citation practices remain inconsistent even among journals with explicit data availability policies. The FORCE11 Data Citation Principles [@FORCE11_2014JDDCP; @Starr2015DataCitation] called for datasets to be treated as first-class research outputs with persistent identifiers, but adoption has been slow. We recommend two complementary interventions: repositories should make dataset DOIs visually prominent and generate pre-formatted citation snippets, and journals should require that datasets used in analysis appear in the reference list with persistent identifiers.

### 4.7 Limitations

Several limitations bear on the interpretation of our results.

**LLM classification accuracy.** Our classifier is subject to both false positives and false negatives. We applied conservative prompt rules designed to minimize false positives, which likely pushes our estimates toward under-counting.

**Incomplete text retrieval.** Approximately 8% of papers could not have their full text retrieved. The majority (73%) are recent publications (2024–2026) not yet indexed by full-text sources, with the remainder primarily paywalled Elsevier and Wiley journals. These are classified as NEITHER by default, systematically underestimating reuse, particularly for recent papers.

**Source archive ambiguity.** Approximately 25% of reuse papers did not specify which repository they used to access data.

**Citation-only tracking.** Researchers who download DANDI data without publishing are invisible to this approach.

**Survival analysis censoring.** Many dandisets are fewer than three years old, meaning their full reuse lifetime has not been observed.

**Recency bias in citation indexing.** OpenAlex and Europe PMC have indexing delays of weeks to months. Papers published in the most recent 6–12 months are systematically underrepresented. We applied a six-month analysis cutoff for survival analyses, but reuse counts for 2025–2026 should be understood as lower bounds.

**Incomplete dandiset-paper linkage for new dandisets.** Many recent dandisets lack associated publications because the paper is still in preparation or review. Our 65% linkage rate is a lower bound that will improve over time as papers are published and metadata is updated.

Together, these biases indicate that our estimates are conservative: the true volume of DANDI data reuse is in all likelihood higher than reported here.

---

## 5. Conclusions

This study provides the first comprehensive, repository-scale quantification of neuroscience data reuse on the DANDI Archive. By combining automated paper retrieval, citation graph traversal, and LLM-based classification across over 19,000 citing paper pairs, we identified 1,234 reuse events involving 944 unique papers, a scale that would have been impossible to document by manual curation. The findings establish that open neurophysiology data sharing through DANDI is generating substantial downstream science: reuse is dominated by groups independent of the original depositors (71%), is taxonomically diverse across eight scientifically meaningful reuse types, and is growing rapidly, with publication counts increasing nearly 20-fold between 2020 and 2025.

Several findings carry direct policy and infrastructure implications. The four-year discovery lag between dandiset creation and peak different-lab reuse should recalibrate funder expectations: short-horizon evaluations of data sharing mandates will inevitably underestimate return on investment, and assessment timelines of five or more years are needed to capture the bulk of reuse activity. The dataset citation gap, in which 92% of secondary papers cite only the companion journal article rather than the archived dataset, is an urgent problem for both credit attribution and automated impact tracking. We recommend that repositories surface dataset DOIs more prominently and that journals amend data availability requirements to mandate structured dataset citations in reference lists.

The analysis pipeline developed for this study, available as open-source software at github.com/catalystneuro/find_reuse, is continuously updatable and can be applied to any repository whose datasets are linked to primary publications indexed in OpenAlex or Europe PMC. Planned extensions include application to OpenNeuro and CRCNS to enable cross-repository comparison of reuse rates, construction of a real-time tracking dashboard integrated into the DANDI web interface, and deeper integration of publication-based reuse tracking with DANDI's download analytics.

---

## Figures

- **Figure 1.** Phase 2 citation analysis pipeline (`output/phase2_citation_flow.png`)
- **Figure 2.** Different-lab reuse: 6-panel overview (`output/figures/combined_different_lab.png`)
  - Panel A: Source archive distribution
  - Panel B: Top journals
  - Panel C: Cumulative reuse over time
  - Panel D: Reuse papers by year
  - Panel E: Mean Cumulative Function
  - Panel F: Reuse rate (events/dandiset/yr)
- **Figure 3.** Reuse type distribution (`output/figures/reuse_type.png`)
- **Figure 4.** Modeling and projections, 2×2 (`output/figures/reuse_rate_model.png`)
  - Panel A: MCF model fits (Richards + saturating exponential)
  - Panel B: Reuse rate with data points
  - Panel C: Dandiset growth projection (power law)
  - Panel D: Projected cumulative DANDI reuse to 2029
- **Figure 5.** Andersen-Gill Cox PH forest plot: predictors of different-lab reuse (`output/figures/andersen_gill_forest.png`)

## Supplementary Figures

- **Figure S1.** Phase 1: Dandiset-to-paper linkage (`output/dandiset_coverage_flow.png`)
- **Figure S2.** Paper text retrieval pipeline (`output/paper_fetching_flow.png`)
- **Figure S3.** How papers reference dandisets (`output/dandiset_reference_flow.png`)
- **Figure S4.** Same-lab reuse: 6-panel overview (`output/figures/combined_same_lab.png`)
- **Figure S5.** Combined (all labs) reuse: 6-panel overview (`output/figures/combined_all_labs.png`)
- **Figure S6.** Survival analysis: KM + MCF (`output/figures/survival_different_lab.png`)
- **Figure S7.** Download metrics vs. publication-based reuse (`output/figures/downloads_vs_reuse.png`)
