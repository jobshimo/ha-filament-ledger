"""The display form of a print's name — the reader-facing derivation.

The raw name is an identity: `TrackPrintJob` correlates endings to starts by it and the
free-text history filter matches it as stored, so nothing here ever touches what the
ledger keeps. `display_job_name` derives what a human reads — the cloud task id and the
slicer extension are the file's business — and these tests pin exactly how far that
derivation is allowed to go.
"""

from __future__ import annotations

from custom_components.filament_ledger.application.query import display_job_name


class TestDisplayJobName:
    def test_strips_the_cloud_task_id_and_the_extension(self) -> None:
        """The downloaded-file form — what every historical row was named with."""
        assert display_job_name("2429842-Royal Crest.gcode") == "Royal Crest"

    def test_strips_a_prefix_without_an_extension(self) -> None:
        assert display_job_name("123-benchy") == "benchy"

    def test_strips_an_extension_without_a_prefix(self) -> None:
        assert display_job_name("vase_final.3mf") == "vase_final"

    def test_extensions_are_stripped_case_insensitively(self) -> None:
        assert display_job_name("Vase.3MF") == "Vase"
        assert display_job_name("part.GCode") == "part"

    def test_a_doubled_extension_loses_only_the_final_one(self) -> None:
        """`vase.gcode.3mf` names a file whose inner form is still a name — one strip,
        so the derivation never eats further into the name than the last extension."""
        assert display_job_name("vase_final.gcode.3mf") == "vase_final.gcode"

    def test_the_slicer_form_keeps_its_dots_and_percent(self) -> None:
        """`gcode_file`'s own form: the leading `0.28mm` is a measurement, not an id."""
        assert display_job_name("0.28mm layer, 2 walls, 15% infill.3mf") == (
            "0.28mm layer, 2 walls, 15% infill"
        )

    def test_a_name_with_neither_artefact_is_untouched(self) -> None:
        assert display_job_name("Royal Crest") == "Royal Crest"

    def test_the_unknown_print_sentinel_is_untouched(self) -> None:
        """`bambu_gateway.UNKNOWN_JOB_NAME`, by value rather than by import — this suite
        runs without Home Assistant. The sentinel carries neither artefact, so it passes
        through by construction rather than by a special case."""
        assert display_job_name("unknown print") == "unknown print"

    def test_a_bare_numeric_prefix_is_a_name_not_a_prefix(self) -> None:
        """`1234-` has nothing after the dash to be the name; stripping would leave
        nothing, so nothing is stripped."""
        assert display_job_name("1234-") == "1234-"

    def test_a_prefix_followed_by_a_space_is_left_alone(self) -> None:
        """The id form never carries a space after the dash; a name that does is a name,
        and half-stripping it would leave a leading space nobody typed."""
        assert display_job_name("123- spaced out") == "123- spaced out"

    def test_a_name_reduced_to_nothing_comes_back_verbatim(self) -> None:
        """Odd shown raw; a rendering bug shown blank."""
        assert display_job_name("123-.gcode") == "123-.gcode"
        assert display_job_name(".3mf") == ".3mf"

    def test_the_empty_string_stays_the_empty_string(self) -> None:
        assert display_job_name("") == ""

    def test_digits_alone_are_a_name(self) -> None:
        assert display_job_name("12345") == "12345"

    def test_unicode_survives_both_strips(self) -> None:
        assert display_job_name("42-Pieza única.3mf") == "Pieza única"

    def test_interior_dashes_belong_to_the_name(self) -> None:
        assert display_job_name("2429842-Royal-Crest.gcode") == "Royal-Crest"
