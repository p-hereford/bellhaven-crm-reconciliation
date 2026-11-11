from crm_writer import apply_reparent

try:
    apply_reparent("ownership:001U6RW32TY0WSXZZB")

except ValueError as error:
    print("SAFETY CHECK PASSED")
    print("Blocked because:", error)

else:
    raise AssertionError("CHOW proposal was not blocked!")