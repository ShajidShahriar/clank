"""What is specific to the Chroma store (task I-4.5b). The shared behaviour is in test_vector_store.py."""
import chromadb
import pytest
from chromadb.config import Settings

from vectorstore import ChromaVectorStore, open_project_store


def test_vectors_survive_closing_and_reopening(tmp_path):
    first = ChromaVectorStore(tmp_path / "chroma", "project_1")
    first.upsert(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
    del first
    again = ChromaVectorStore(tmp_path / "chroma", "project_1")
    assert again.ids() == {"a", "b"}
    assert again.query([1.0, 0.0], 1)[0][0] == "a"


def test_two_projects_in_one_folder_keep_their_own_vectors(tmp_path):
    one, two = open_project_store(tmp_path, 1), open_project_store(tmp_path, 2)
    one.upsert(["same-id"], [[1.0, 0.0]])
    two.upsert(["same-id", "only-in-two"], [[0.0, 1.0], [1.0, 1.0]])
    assert one.ids() == {"same-id"} and two.ids() == {"same-id", "only-in-two"}
    two.clear()
    assert one.ids() == {"same-id"} and two.ids() == set()


def test_projects_are_stored_in_a_chroma_folder_under_the_base_folder(tmp_path):
    open_project_store(tmp_path, 7).upsert(["a"], [[1.0, 0.0]])
    assert (tmp_path / "chroma").is_dir()


def test_a_collection_that_was_made_with_the_default_l2_distance_is_refused(tmp_path):
    # get_or_create_collection ignores the settings you pass when the collection already exists, so without a check
    # an old L2 collection would quietly change what every score means.
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    client.create_collection("project_1", embedding_function=None)   # Chroma's default space is l2
    with pytest.raises(RuntimeError, match="cosine"):
        ChromaVectorStore(tmp_path / "chroma", "project_1")


def test_the_collection_really_uses_cosine(tmp_path):
    store = ChromaVectorStore(tmp_path / "chroma", "project_1")
    store.upsert(["unit", "huge"], [[1.0, 0.0], [1000.0, 0.0]])
    scores = dict(store.query([1.0, 0.0], 2))
    assert scores["unit"] == pytest.approx(scores["huge"], abs=1e-4)   # with L2 these would be wildly different


def test_telemetry_is_off(tmp_path):
    store = ChromaVectorStore(tmp_path / "chroma", "project_1")
    assert store._client.get_settings().anonymized_telemetry is False


def test_clear_then_a_new_vector_length_works(tmp_path):
    store = ChromaVectorStore(tmp_path / "chroma", "project_1")
    store.upsert(["a"], [[1.0, 0.0]])
    store.clear()
    store.upsert(["a"], [[1.0, 0.0, 0.0, 0.0]])
    assert store.count() == 1 and store.query([1.0, 0.0, 0.0, 0.0], 1)[0][0] == "a"


def test_the_signature_survives_closing_and_reopening(tmp_path):
    first = ChromaVectorStore(tmp_path / "chroma", "project_1")
    first.set_signature("qwen3-embedding:0.6b", 1024)
    first.upsert(["a"], [[1.0, 0.0]])
    del first
    again = ChromaVectorStore(tmp_path / "chroma", "project_1")
    assert again.signature() == ("qwen3-embedding:0.6b", 1024)
    assert again.ids() == {"a"}


def test_setting_the_signature_does_not_change_the_distance_or_lose_vectors(tmp_path):
    store = ChromaVectorStore(tmp_path / "chroma", "project_1")
    store.upsert(["unit", "huge"], [[1.0, 0.0], [1000.0, 0.0]])
    store.set_signature("m", 2)
    scores = dict(store.query([1.0, 0.0], 2))
    assert scores["unit"] == pytest.approx(scores["huge"], abs=1e-4)      # still cosine
    assert store.ids() == {"unit", "huge"}
