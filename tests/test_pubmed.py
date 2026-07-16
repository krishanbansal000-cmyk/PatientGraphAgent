import unittest
from unittest.mock import patch

from assistant.pubmed import PubMedClient


SAMPLE_XML = """\
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID Version="1">12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue>
            <PubDate><Year>2025</Year><Month>Jan</Month><Day>7</Day></PubDate>
          </JournalIssue>
          <Title>Journal of Clinical Evidence</Title>
        </Journal>
        <ArticleTitle>Effects of <i>metformin</i> in adults</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text.</AbstractText>
          <AbstractText Label="RESULTS">Results text.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author>
          <Author><CollectiveName>Evidence Group</CollectiveName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType>Systematic Review</PublicationType>
        </PublicationTypeList>
      </Article>
      <MeshHeadingList>
        <MeshHeading><DescriptorName>Metformin</DescriptorName></MeshHeading>
      </MeshHeadingList>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/example</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


class PubMedClientTests(unittest.TestCase):
    def test_parses_structured_article_metadata(self):
        article = PubMedClient.parse_articles(SAMPLE_XML)[0]

        self.assertEqual(article["pmid"], "12345678")
        self.assertEqual(article["title"], "Effects of metformin in adults")
        self.assertIn("BACKGROUND: Background text.", article["abstract"])
        self.assertEqual(article["journal"], "Journal of Clinical Evidence")
        self.assertEqual(article["published_at"], "2025-01-07")
        self.assertEqual(article["authors"], ["Ada Lovelace", "Evidence Group"])
        self.assertEqual(article["article_types"], ["Systematic Review"])
        self.assertEqual(article["mesh_terms"], ["Metformin"])
        self.assertEqual(article["doi"], "10.1000/example")

    def test_search_applies_safe_filters_and_preserves_relevance_order(self):
        client = PubMedClient(api_key="", tool="test-client")
        search_response = {"esearchresult": {"idlist": ["12345678"]}}

        with patch.object(client, "_request_json", return_value=search_response) as search_call:
            with patch.object(client, "_request_text", return_value=SAMPLE_XML) as fetch_call:
                articles = client.search(
                    "type 2 diabetes metformin outcomes unique-test-query",
                    max_results=20,
                    date_start="2024-01-01",
                    date_end="2025-12-31",
                    article_type="systematic review",
                )

        self.assertEqual([item["pmid"] for item in articles], ["12345678"])
        search_params = search_call.call_args.args[1]
        self.assertIn("systematic review[Publication Type]", search_params["term"])
        self.assertEqual(search_params["retmax"], 8)
        self.assertEqual(search_params["mindate"], "2024/01/01")
        self.assertEqual(search_params["maxdate"], "2025/12/31")
        self.assertEqual(fetch_call.call_args.args[1]["id"], "12345678")

    def test_rejects_invalid_queries_and_filters(self):
        client = PubMedClient(api_key="")

        with self.assertRaisesRegex(ValueError, "query is required"):
            client.search(" ")
        with self.assertRaisesRegex(ValueError, "ISO date"):
            client.search("diabetes", date_start="July 1")
        with self.assertRaisesRegex(ValueError, "Unsupported article_type"):
            client.search("diabetes", article_type="case report")
        with self.assertRaisesRegex(ValueError, "on or before"):
            client.search("diabetes", date_start="2025-01-01", date_end="2024-01-01")


if __name__ == "__main__":
    unittest.main()
